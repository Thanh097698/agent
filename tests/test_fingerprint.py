import ctypes
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import fingerprint
from agent_common import DEFAULT_ORIGIN, atomic_json
from agent_runtime import AgentRuntime
from fingerprint import FingerprintError, ReaderPresence, capture_job, finger_box, mock_reader, render_print
from worker_process import WorkerProcess
import test_agent


class PresenceTests(unittest.TestCase):
    def test_matches_zkteco_vendor_only(self):
        ids = ["USB\\VID_046D&PID_C08B\\6&1", "USB\\VID_1B55&PID_0124\\5&2"]
        self.assertTrue(ReaderPresence(lister=lambda: ids).present())
        self.assertFalse(ReaderPresence(lister=lambda: ids[:1]).present())
        self.assertFalse(ReaderPresence(lister=lambda: ["USB\\VID_11B55&PID_0124\\1"]).present())

    def test_result_cached_between_health_calls(self):
        calls = []
        reader = ReaderPresence(lister=lambda: calls.append(1) or ["USB\\VID_1B55&PID_0124\\1"])
        for _ in range(5):
            reader.present()
        self.assertEqual(len(calls), 1)
        reader.checked = time.monotonic() - fingerprint.PRESENCE_TTL - 1
        reader.present()
        self.assertEqual(len(calls), 2)

    def test_listing_failure_means_absent(self):
        def broken():
            raise OSError()
        self.assertFalse(ReaderPresence(lister=broken).present())

    def test_mock_reports_reader(self):
        self.assertTrue(ReaderPresence(mock=True, lister=lambda: []).present())

    @unittest.skipUnless(os.name == "nt", "SetupAPI is Windows only")
    def test_setupapi_lists_present_usb_devices(self):
        for item in fingerprint._usb_instance_ids():
            self.assertTrue(item.upper().startswith("USB\\"))


class RenderTests(unittest.TestCase):
    def test_blank_glass_has_no_finger(self):
        self.assertIsNone(finger_box(b"\xf2" * (300 * 375), 300, 375))
        with self.assertRaises(FingerprintError) as caught:
            render_print(b"\xf2" * (300 * 375), 300, 375)
        self.assertEqual(caught.exception.code, "NO_FINGER")

    def test_crops_to_finger_on_white_paper(self):
        raw, width, height = mock_reader()
        picture = render_print(raw, width, height)
        self.assertLess(picture.width, width)
        self.assertLess(picture.height, height)
        self.assertEqual(picture.getpixel((0, 0)), 255)

    def test_dark_background_sensor_is_inverted(self):
        raw, width, height = mock_reader()
        inverted = bytes(255 - value for value in raw)
        self.assertEqual(finger_box(inverted, width, height), finger_box(raw, width, height))
        self.assertEqual(render_print(inverted, width, height).getpixel((0, 0)), 255)

    def test_mock_job_returns_one_jpeg(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = capture_job(Path(temporary), {}, True)
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["images"]), 1)
            item = result["images"][0]
            self.assertRegex(item["name"], r"^diem-chi-\d{8}-\d{6}\.jpg$")
            path = Path(temporary) / "000.jpg"
            self.assertEqual(path.stat().st_size, item["size"])
            with Image.open(path) as picture:
                self.assertEqual(picture.format, "JPEG")


class FakeFunction:
    def __init__(self, body):
        self.body, self.calls = body, 0
        self.argtypes, self.restype = None, None

    def __call__(self, *args):
        self.calls += 1
        return self.body(*args)


class FakeLibrary:
    def __init__(self, init=0, count=1, handle=7, codes=(-8, -8, 0)):
        raw, width, height = mock_reader()
        codes = list(codes)

        def acquire(_, image, size):
            code = codes.pop(0) if codes else codes_last
            if code == 0:
                ctypes.memmove(image, raw, size)
            return code

        def parameter(_, code, value, size):
            number = {1: width, 2: height}[code]
            ctypes.memmove(value, number.to_bytes(4, "little"), 4)
            return 0

        codes_last = codes[-1] if codes else -8
        self.ZKFPM_Init = FakeFunction(lambda: init)
        self.ZKFPM_Terminate = FakeFunction(lambda: 0)
        self.ZKFPM_GetDeviceCount = FakeFunction(lambda: count)
        self.ZKFPM_OpenDevice = FakeFunction(lambda index: handle)
        self.ZKFPM_CloseDevice = FakeFunction(lambda device: 0)
        self.ZKFPM_GetParameters = FakeFunction(parameter)
        self.ZKFPM_AcquireFingerprintImage = FakeFunction(acquire)


class ReaderTests(unittest.TestCase):
    def read(self, library, seconds=2.0):
        with patch.object(ctypes, "WinDLL", create=True, return_value=library), patch.object(fingerprint.time, "sleep"):
            return fingerprint._read_reader(seconds)

    def failure(self, library, seconds=2.0):
        with self.assertRaises(FingerprintError) as caught:
            self.read(library, seconds)
        return caught.exception.code

    def test_waits_for_finger_then_closes(self):
        library = FakeLibrary()
        raw, width, height = self.read(library)
        self.assertEqual((width, height), (300, 375))
        self.assertEqual(len(raw), width * height)
        self.assertEqual(library.ZKFPM_AcquireFingerprintImage.calls, 3)
        self.assertEqual(library.ZKFPM_CloseDevice.calls, 1)
        self.assertEqual(library.ZKFPM_Terminate.calls, 1)

    def test_missing_driver(self):
        with patch.object(ctypes, "WinDLL", create=True, side_effect=OSError()):
            with self.assertRaises(FingerprintError) as caught:
                fingerprint._read_reader(1)
        self.assertEqual(caught.exception.code, "READER_DRIVER")

    def test_errors_are_mapped_and_device_released(self):
        self.assertEqual(self.failure(FakeLibrary(init=-3)), "NO_READER")
        self.assertEqual(self.failure(FakeLibrary(init=-1)), "READER_DRIVER")
        self.assertEqual(self.failure(FakeLibrary(count=0)), "NO_READER")
        self.assertEqual(self.failure(FakeLibrary(handle=None)), "READER_OPEN")
        library = FakeLibrary(codes=(-8, -5))
        self.assertEqual(self.failure(library), "READER_ERROR")
        self.assertEqual(library.ZKFPM_CloseDevice.calls, 1)
        self.assertEqual(library.ZKFPM_Terminate.calls, 1)

    def test_gives_up_when_no_finger(self):
        library = FakeLibrary(codes=(-8,))
        self.assertEqual(self.failure(library, seconds=0), "NO_FINGER")
        self.assertEqual(library.ZKFPM_CloseDevice.calls, 1)


class RuntimeFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = AgentRuntime(Path(self.temp.name), mock=True, worker_factory=test_agent.FakeProcess)
        self.grant = {"clinicKey": "a", "label": "A"}

    def tearDown(self):
        self.runtime.close()
        self.temp.cleanup()

    def test_fingerprint_job_returns_image_and_frees_device(self):
        snapshot = self.runtime.start_job("owner", self.grant, {"kind": "fingerprint", "requestId": "finger"})
        self.assertEqual(snapshot["kind"], "fingerprint")
        job = self.runtime.jobs[snapshot["jobId"]]
        self.assertLessEqual(job.deadline - time.monotonic(), fingerprint.FINGER_SECONDS + 15)
        self.assertEqual(json.loads((job.directory / "request.json").read_text(encoding="utf-8"))["params"],
                         {"seconds": fingerprint.FINGER_SECONDS})
        job.process.complete(capture_job(job.directory, {}, True))
        self.runtime.tick()
        self.assertEqual(self.runtime.snapshot("owner", job.id)["state"], "completed")
        self.assertFalse(self.runtime.busy())
        stream, size = self.runtime.open_image("owner", job.id, 0)
        with stream:
            self.assertEqual(stream.read(2), b"\xff\xd8")
        self.runtime.release("owner", job.id)

    def test_reader_failure_is_reported(self):
        snapshot = self.runtime.start_job("owner", self.grant, {"kind": "fingerprint", "requestId": "finger"})
        job = self.runtime.jobs[snapshot["jobId"]]
        job.process.complete(FingerprintError("NO_FINGER").result())
        self.runtime.tick()
        result = self.runtime.snapshot("owner", job.id)
        self.assertEqual((result["state"], result["code"]), ("failed", "NO_FINGER"))
        self.assertFalse(self.runtime.busy())

    def test_health_reports_reader(self):
        case = test_agent.HttpTests("test_health_and_cors")
        case.setUp()
        try:
            status, _, raw = case.request("GET", "/health")
            self.assertEqual(status, 200)
            self.assertIs(json.loads(raw)["fingerprint"], True)
        finally:
            case.tearDown()


class RealFingerprintWorkerTests(unittest.TestCase):
    def run_worker(self, mock):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            atomic_json(directory / "request.json", {"kind": "fingerprint", "mock": mock, "params": {"seconds": 0.5}})
            worker = WorkerProcess(directory)
            try:
                deadline = time.monotonic() + 20
                while worker.alive() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(worker.alive())
                return json.loads((directory / "result.json").read_text(encoding="utf-8"))
            finally:
                worker.terminate()

    def test_mock_child_returns_print(self):
        result = self.run_worker(True)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["images"]), 1)

    @unittest.skipUnless(os.name == "nt", "ZKFinger driver is Windows only")
    def test_real_child_without_reader_reports_clear_error(self):
        result = self.run_worker(False)
        self.assertFalse(result["ok"])
        self.assertIn(result["code"], {"READER_DRIVER", "NO_READER", "NO_FINGER"})
        self.assertEqual(result["error"], fingerprint.MESSAGES[result["code"]])


if __name__ == "__main__":
    unittest.main()
