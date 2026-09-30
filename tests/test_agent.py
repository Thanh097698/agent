from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_common import AgentError, DEFAULT_ORIGIN, MAX_JSON, Pairings, atomic_json, safe_remove_job
from agent_runtime import AgentRuntime
from agent_server import AgentServer
from twain_worker import FrameWriter, mock_job
from worker_process import WorkerProcess


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.json"
        self.p = Pairings(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def connect(self, clinic="clinic_a"):
        result = self.p.pair(self.p.new_code(), clinic, clinic, DEFAULT_ORIGIN)
        session = self.p.open_session(result["pairToken"], DEFAULT_ORIGIN)
        return result, session

    def test_codes_single_use_and_secrets_hashed(self):
        code = self.p.new_code()
        token = self.p.pair(code, "a", "A", DEFAULT_ORIGIN)["pairToken"]
        self.assertNotIn(token, self.path.read_text())
        with self.assertRaises(AgentError):
            self.p.pair(code, "a", "A", DEFAULT_ORIGIN)

    def test_wrong_codes_have_attempt_limit(self):
        code = self.p.new_code()
        bad = "11111111" if code != "11111111" else "22222222"
        for _ in range(5):
            with self.assertRaises(AgentError):
                self.p.pair(bad, "a", "A", DEFAULT_ORIGIN)
        with self.assertRaises(AgentError):
            self.p.pair(code, "a", "A", DEFAULT_ORIGIN)

    def test_expired_code(self):
        code = self.p.new_code()
        self.p.code_until = 0
        with self.assertRaises(AgentError):
            self.p.pair(code, "a", "A", DEFAULT_ORIGIN)

    def test_origin_binding(self):
        grant, session = self.connect()
        for fn in (lambda: self.p.open_session(grant["pairToken"], "https://evil.example"),
                   lambda: self.p.authorize(session["sessionToken"], "https://evil.example")):
            with self.assertRaises(AgentError):
                fn()

    def test_revoke_invalidates_pair_and_session(self):
        grant, session = self.connect()
        self.p.forget_all()
        for fn in (lambda: self.p.open_session(grant["pairToken"], DEFAULT_ORIGIN),
                   lambda: self.p.authorize(session["sessionToken"], DEFAULT_ORIGIN)):
            with self.assertRaises(AgentError):
                fn()

    def test_session_expires(self):
        _, session = self.connect()
        for value in self.p.sessions.values():
            value["expiresAt"] = 0
        with self.assertRaises(AgentError):
            self.p.authorize(session["sessionToken"], DEFAULT_ORIGIN)

    def test_pair_expiry_rechecked_for_existing_session(self):
        _, session = self.connect()
        for value in self.p.data["grants"].values():
            value["expiresAt"] = 0
        with self.assertRaises(AgentError):
            self.p.authorize(session["sessionToken"], DEFAULT_ORIGIN)

    def test_session_count_is_bounded(self):
        grant, _ = self.connect()
        for _ in range(15):
            self.p.open_session(grant["pairToken"], DEFAULT_ORIGIN)
        self.assertEqual(len(self.p.sessions), 8)

    def test_persistent_grant_not_persistent_session(self):
        grant, session = self.connect()
        reloaded = Pairings(self.path)
        reloaded.open_session(grant["pairToken"], DEFAULT_ORIGIN)
        with self.assertRaises(AgentError):
            reloaded.authorize(session["sessionToken"], DEFAULT_ORIGIN)

    def test_machine_settings_isolated_and_copied(self):
        self.p.save_device("a", "1", "Driver A")
        self.p.save_device("b", "1", "Driver B")
        returned = self.p.settings("a")
        returned["devices"]["1"] = "tampered"
        self.assertEqual(self.p.settings("a")["devices"]["1"], "Driver A")
        self.assertEqual(self.p.settings("b")["devices"]["1"], "Driver B")


class FakeProcess:
    def __init__(self, directory):
        self.directory = directory
        self.running = True
        self.can_stop = True

    def alive(self):
        return self.running

    def terminate(self):
        if self.can_stop:
            self.running = False
        return not self.running

    def close(self):
        pass

    def complete(self, result):
        atomic_json(self.directory / "result.json", result)
        self.running = False


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = AgentRuntime(Path(self.temp.name), mock=True, worker_factory=FakeProcess)
        self.grant = {"clinicKey": "a", "label": "A"}
        self.runtime.pairings.save_device("a", "1", "ViDental Mock Sensor")

    def tearDown(self):
        for job in self.runtime.jobs.values():
            if isinstance(job.process, FakeProcess):
                job.process.can_stop = True
        self.runtime.close()
        self.temp.cleanup()

    def start(self, request="request-1", owner="owner", **kwargs):
        return self.runtime.start_job(owner, self.grant, {"kind": "capture", "requestId": request, "deviceId": "1", **kwargs})

    def active(self):
        return self.runtime.jobs[self.runtime.active]

    def test_shutdown_rejects_late_requests(self):
        self.runtime.close()
        with self.assertRaises(AgentError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "SHUTTING_DOWN")
        self.assertFalse(self.runtime.busy())

    def test_busy_and_idempotency(self):
        job = self.start()
        self.assertEqual(self.start()["jobId"], job["jobId"])
        with self.assertRaises(AgentError):
            self.start("request-2")

    def test_invalid_input_never_sets_busy(self):
        for value in [0, 101, True, "92"]:
            with self.assertRaises(AgentError):
                self.start(jpegQuality=value)
            self.assertFalse(self.runtime.busy())

    def test_arbitrary_source_path_and_test_controls_rejected(self):
        for key in ("sourceName", "localSaveDir", "_testHang", "_testCrash"):
            with self.assertRaises(AgentError):
                self.start(**{key: "bad"})
            self.assertFalse(self.runtime.busy())

    def test_unconfigured_device(self):
        with self.assertRaises(AgentError):
            self.start(deviceId="2")

    def test_owner_cannot_read_cancel_release_or_download_another_job(self):
        job = self.start()["jobId"]
        for fn in (lambda: self.runtime.snapshot("other", job), lambda: self.runtime.cancel("other", job),
                   lambda: self.runtime.release("other", job), lambda: self.runtime.open_image("other", job, 0)):
            with self.assertRaises(AgentError) as caught:
                fn()
            self.assertEqual(caught.exception.status, 404)

    def test_cancel_then_reuse(self):
        job = self.start()["jobId"]
        self.runtime.cancel("owner", job)
        self.assertTrue(self.runtime.busy())
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot("owner", job)["state"], "cancelled")
        self.assertFalse(self.runtime.busy())
        self.start("next")

    def test_cancel_before_request_arrives(self):
        self.runtime.cancel_request("owner", "request-1")
        with self.assertRaises(AgentError) as caught:
            self.start()
        self.assertEqual(caught.exception.code, "REQUEST_CANCELLED")
        self.assertFalse(self.runtime.busy())

    def test_cancel_does_not_free_stuck_driver(self):
        job = self.start()["jobId"]
        self.active().process.can_stop = False
        self.runtime.cancel("owner", job)
        self.runtime.tick()
        self.assertTrue(self.runtime.busy())
        self.active().process.can_stop = True
        self.runtime.tick()
        self.assertFalse(self.runtime.busy())

    def test_timeout_cleans_and_releases(self):
        job = self.start()["jobId"]
        directory = self.active().directory
        self.active().deadline = 0
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot("owner", job)["state"], "timed_out")
        self.assertFalse(directory.exists())
        self.assertFalse(self.runtime.busy())

    def test_crashed_worker(self):
        job = self.start()["jobId"]
        self.active().process.running = False
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot("owner", job)["code"], "WORKER_CRASHED")
        self.assertFalse(self.runtime.busy())

    def test_start_failure_releases(self):
        def broken(_):
            raise OSError()
        self.runtime.worker_factory = broken
        self.assertEqual(self.start()["state"], "failed")
        self.assertFalse(self.runtime.busy())

    def test_release_cannot_remove_running_job(self):
        job = self.start()["jobId"]
        with self.assertRaises(AgentError):
            self.runtime.release("owner", job)
        self.assertTrue(self.active().directory.exists())

    def test_completed_images_download_and_release(self):
        job_id = self.start()["jobId"]
        job = self.active()
        result = mock_job("capture", job.directory, {"jpegQuality": 92})
        job.process.complete(result)
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot("owner", job_id)["state"], "completed")
        stream, size = self.runtime.open_image("owner", job_id, 0)
        with stream:
            self.assertEqual(stream.read(2), b"\xff\xd8")
        self.assertGreater(size, 0)
        self.runtime.release("owner", job_id)
        with self.assertRaises(AgentError):
            self.runtime.open_image("owner", job_id, 0)

    def test_completed_invalid_metadata_rejected(self):
        self.start()
        job = self.active()
        result = mock_job("capture", job.directory, {"jpegQuality": 92})
        result["images"][0]["name"] = "../outside.jpg"
        job.process.complete(result)
        self.runtime.tick()
        self.assertEqual(job.state, "failed")

    def test_released_jobs_do_not_fill_image_quota(self):
        for i in range(12):
            job = self.start(str(i))["jobId"]
            self.active().process.complete({"ok": True, "images": []})
            self.runtime.tick()
            self.runtime.release("owner", job)
        self.start("next")

    def test_ttl_cleanup(self):
        job = self.start()["jobId"]
        self.active().process.complete({"ok": True, "images": []})
        self.runtime.tick()
        self.runtime.jobs[job].finished_at = time.monotonic() - 901
        self.runtime.tick()
        self.assertNotIn(job, self.runtime.jobs)

    def test_cannot_save_unknown_source(self):
        with self.assertRaises(AgentError):
            self.runtime.save_device(self.grant, {"deviceId": "1", "sourceName": "unknown"})

    def test_folder_requires_native_selection_and_current_job(self):
        job = self.runtime.start_job("owner", self.grant, {"kind": "folder", "requestId": "folder"})
        self.runtime.cancel("owner", job["jobId"])
        self.active().process.complete({"ok": True, "folder": self.temp.name})
        self.runtime.tick()
        self.assertEqual(self.runtime.pairings.settings("a")["folder"], "")
        self.runtime.tick()

    def test_local_folder_and_copy(self):
        local = Path(self.temp.name) / "copies"
        local.mkdir()
        job = self.runtime.start_job("owner", self.grant, {"kind": "folder", "requestId": "folder"})
        self.active().process.complete({"ok": True, "folder": str(local.resolve())})
        self.runtime.tick()
        self.assertEqual(self.runtime.pairings.settings("a")["folder"], str(local.resolve()))
        capture = self.start()
        result = mock_job("capture", self.active().directory, {"jpegQuality": 92, "localFolder": str(local)})
        self.assertEqual(len(list(local.glob("*.jpg"))), 1)
        self.assertEqual(result["warning"], "")

    def test_folder_timeout_terminates_its_own_worker(self):
        self.runtime.start_job("owner", self.grant, {"kind": "folder", "requestId": "folder"})
        job = self.active()
        job.deadline = 0
        self.runtime.tick()
        self.assertEqual(job.state, "timed_out")
        self.assertFalse(job.process.alive())
        self.assertFalse(self.runtime.busy())

    def test_revoke_cancels_active(self):
        self.start()
        self.runtime.revoke_pairings()
        self.runtime.tick()
        self.assertFalse(self.runtime.busy())

    def test_safe_cleanup_refuses_unowned_paths(self):
        with self.assertRaises(ValueError):
            safe_remove_job(self.runtime.root, Path(self.temp.name))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = AgentServer(("127.0.0.1", 0), {DEFAULT_ORIGIN}, request_seconds=0.7)
        self.runtime = AgentRuntime(Path(self.temp.name), mock=True, worker_factory=FakeProcess)
        self.server.runtime = self.runtime
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        pair = self.runtime.pairings.pair(self.runtime.pairings.new_code(), "a", "A", DEFAULT_ORIGIN)
        self.token = self.runtime.pairings.open_session(pair["pairToken"], DEFAULT_ORIGIN)["sessionToken"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.runtime.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        defaults = {"Origin": DEFAULT_ORIGIN, "Authorization": "Bearer " + self.token}
        if body is not None:
            defaults["Content-Type"] = "application/json"
            body = json.dumps(body)
        defaults.update(headers or {})
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        connection.close()
        return result

    def test_health_and_cors(self):
        status, headers, raw = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], DEFAULT_ORIGIN)
        self.assertEqual(json.loads(raw)["protocol"], 2)

    def test_preflight(self):
        status, headers, _ = self.request("OPTIONS", "/jobs")
        self.assertEqual(status, 204)
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])

    def test_evil_and_null_origin(self):
        for origin in ("https://evil.example", "null", "http://localhost:5173"):
            status, headers, _ = self.request("POST", "/jobs", {}, {"Origin": origin})
            self.assertEqual(status, 403)
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_host_rebinding_denied(self):
        self.assertEqual(self.request("GET", "/health", headers={"Host": "evil.example"})[0], 403)

    def test_missing_or_invalid_session(self):
        self.assertEqual(self.request("GET", "/settings", headers={"Authorization": "Bearer invalid"})[0], 401)

    def test_pair_token_not_valid_as_session(self):
        token = self.runtime.pairings.pair(self.runtime.pairings.new_code(), "b", "B", DEFAULT_ORIGIN)["pairToken"]
        self.assertEqual(self.request("GET", "/settings", headers={"Authorization": "Bearer " + token})[0], 401)

    def test_queries_and_absolute_url_denied(self):
        for path in ("/health?token=secret", "http://evil.example/health"):
            self.assertEqual(self.request("GET", path)[0], 400)

    def raw(self, request):
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as connection:
            connection.sendall(request.encode())
            return connection.recv(2048).decode(errors="replace")

    def test_negative_huge_duplicate_lengths_and_chunked(self):
        for header, expected in (("Content-Length: -1", "400"), (f"Content-Length: {MAX_JSON + 1}", "413"),
                                 ("Content-Length: 2\r\nContent-Length: 2", "411"),
                                 ("Transfer-Encoding: chunked", "400")):
            raw = self.raw(f"POST /jobs HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {DEFAULT_ORIGIN}\r\n{header}\r\nContent-Type: application/json\r\n\r\n{{}}")
            self.assertIn(expected, raw.splitlines()[0])
            self.assertFalse(self.runtime.busy())

    def test_non_object_json(self):
        self.assertEqual(self.request("POST", "/jobs", [1, 2])[0], 400)
        self.assertFalse(self.runtime.busy())

    def test_body_stall_has_deadline_without_setting_busy(self):
        started = time.monotonic()
        raw = self.raw(f"POST /jobs HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {DEFAULT_ORIGIN}\r\nContent-Length: 100\r\nContent-Type: application/json\r\n\r\n{{")
        self.assertEqual(raw, "")
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertFalse(self.runtime.busy())
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_headers_stall_has_deadline(self):
        started = time.monotonic()
        self.assertEqual(self.raw("GET /health HTTP/1.1\r\nHost:"), "")
        self.assertLess(time.monotonic() - started, 1.8)

    def test_job_is_accepted_without_waiting_for_driver(self):
        self.runtime.pairings.save_device("a", "1", "source")
        started = time.monotonic()
        status, _, raw = self.request("POST", "/jobs", {"kind": "capture", "requestId": "req", "deviceId": "1"})
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(raw)["state"], "running")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_legacy_capture_not_supported(self):
        self.assertEqual(self.request("POST", "/capture", {})[0], 410)


class RealWorkerTests(unittest.TestCase):
    def test_real_mock_child_and_native_process_cancellation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index, params in enumerate(({"jpegQuality": 92}, {"jpegQuality": 92, "_testHang": True},
                                           {"jpegQuality": 92, "_testCrash": True})):
                directory = root / str(index)
                directory.mkdir()
                atomic_json(directory / "request.json", {"kind": "capture", "mock": True, "params": params})
                worker = WorkerProcess(directory)
                if os.name == "nt":
                    self.assertIsNotNone(worker.job_handle)
                if params.get("_testHang"):
                    time.sleep(0.3)
                    self.assertTrue(worker.alive())
                    self.assertTrue(worker.terminate())
                    self.assertFalse(worker.alive())
                else:
                    deadline = time.monotonic() + 15
                    while worker.alive() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    try:
                        self.assertFalse(worker.alive())
                        if params.get("_testCrash"):
                            self.assertFalse((directory / "result.json").exists())
                        else:
                            self.assertTrue(json.loads((directory / "result.json").read_text())["ok"])
                    finally:
                        worker.terminate()


if __name__ == "__main__":
    unittest.main()
