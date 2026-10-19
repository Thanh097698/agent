from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_common import (AgentError, CAPTURE_SECONDS, JOB_TTL_SECONDS, MAX_IMAGE, MAX_IMAGES, MAX_JOB_BYTES,
                          Pairings, atomic_json, identifier, read_json, safe_remove_job, text)
from fingerprint import FINGER_SECONDS, ReaderPresence
from worker_process import WorkerProcess

# Tác vụ trả ảnh về web qua /jobs/<id>/images/<n>.
IMAGE_KINDS = {"capture", "fingerprint"}


@dataclass
class Job:
    id: str
    request_id: str
    owner: str
    clinic_key: str
    kind: str
    directory: Path
    deadline: float
    state: str = "running"
    process: WorkerProcess | None = None
    result: dict = field(default_factory=dict)
    finished_at: float = 0
    cancel_requested: bool = False
    released: bool = False
    stop_warning: bool = False
    cleanup_retry_at: float = 0

    def snapshot(self) -> dict:
        return {"ok": True, "jobId": self.id, "requestId": self.request_id, "kind": self.kind,
                "state": self.state, "released": self.released, **self.result}


class AgentRuntime:
    def __init__(self, data_dir: Path, mock: bool = False, capture_seconds: float = CAPTURE_SECONDS,
                 worker_factory=WorkerProcess, reader: ReaderPresence | None = None):
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.root = self.data_dir / "jobs"
        self.root.mkdir(exist_ok=True)
        self.pairings = Pairings(self.data_dir / "settings.json")
        self.mock = mock
        self.capture_seconds = capture_seconds
        self.worker_factory = worker_factory
        self.reader = reader or ReaderPresence(mock)
        self.lock = threading.RLock()
        self.jobs: dict[str, Job] = {}
        self.active: str | None = None
        self.cancelled_requests: dict[tuple[str, str], float] = {}
        self.sources: list[str] = []
        self.stop_event = threading.Event()
        self.thread = None
        # Only after the server has successfully bound its unique loopback port.
        for child in self.root.iterdir():
            try:
                safe_remove_job(self.root, child)
            except (OSError, ValueError):
                logging.warning("Could not remove an expired imaging work directory")

    def start_monitor(self) -> None:
        self.thread = threading.Thread(target=self._monitor, daemon=True, name="imaging-supervisor")
        self.thread.start()

    def busy(self) -> bool:
        with self.lock:
            return self.active is not None

    def start_job(self, owner: str, grant: dict, body: dict) -> dict:
        kind = body.get("kind")
        if kind not in {"sources", "capture", "folder", "fingerprint"}:
            raise AgentError(400, "INVALID_JOB", "Loại tác vụ không hợp lệ")
        request_id = identifier(body.get("requestId"))
        clinic_key = grant["clinicKey"]
        with self.lock:
            if self.stop_event.is_set():
                raise AgentError(503, "SHUTTING_DOWN", "Bộ kết nối đang tắt")
            self._prune()
            if (owner, request_id) in self.cancelled_requests:
                raise AgentError(409, "REQUEST_CANCELLED", "Yêu cầu đã được hủy")
            for job in self.jobs.values():
                if job.owner == owner and job.request_id == request_id:
                    return job.snapshot()
            self._prune()
            if self.active is not None:
                raise AgentError(409, "BUSY", "Máy đang xử lý một tác vụ khác. Hãy đợi hoặc hủy tác vụ đó.")
            if len(self.jobs) >= 256 or sum(not job.released and job.kind in IMAGE_KINDS for job in self.jobs.values()) >= 8 \
                    or self._disk_usage() > 128 * 1024 * 1024 - MAX_JOB_BYTES:
                raise AgentError(507, "TEMP_STORAGE_FULL", "Ảnh tạm chưa được nhận hết. Hoàn tất lượt trước rồi thử lại.")
            params = {"label": grant.get("label", clinic_key)} if kind == "folder" else {}
            if kind == "fingerprint":
                params = {"seconds": FINGER_SECONDS}
            if kind == "capture":
                device_id = identifier(body.get("deviceId"))
                machine = self.pairings.settings(clinic_key)
                source = machine["devices"].get(device_id)
                if not source:
                    raise AgentError(409, "DEVICE_NOT_CONFIGURED", "Chưa chọn nguồn chụp cho thiết bị trên máy này")
                quality = body.get("jpegQuality", 92)
                if isinstance(quality, bool) or not isinstance(quality, int) or not 1 <= quality <= 100:
                    raise AgentError(400, "INVALID_INPUT", "Chất lượng ảnh không hợp lệ")
                show_ui = body.get("showUi", True)
                if not isinstance(show_ui, bool):
                    raise AgentError(400, "INVALID_INPUT", "Tùy chọn cửa sổ chụp không hợp lệ")
                if any(key in body for key in ("localSaveDir", "sourceName", "_testHang", "_testCrash")):
                    raise AgentError(400, "LOCAL_SETTINGS_REQUIRED", "Nguồn chụp và thư mục lưu phải được cấu hình trên máy")
                params = {"sourceName": source, "showUi": show_ui, "jpegQuality": quality,
                          "filePrefix": text(body.get("filePrefix", "anh"), 80),
                          "localFolder": machine.get("folder", "")}
            job_id = secrets.token_hex(16)
            directory = self.root / job_id
            directory.mkdir()
            seconds = {"capture": self.capture_seconds, "folder": 120, "fingerprint": FINGER_SECONDS + 15}.get(kind, 20)
            seconds = min(seconds, max(0, grant.get("expiresAt", time.time() + seconds) - time.time()))
            job = Job(job_id, request_id, owner, clinic_key, kind, directory, time.monotonic() + seconds)
            self.jobs[job_id] = job
            self.active = job_id
            try:
                atomic_json(directory / "request.json", {"kind": kind, "mock": self.mock, "params": params})
                job.process = self.worker_factory(directory)
            except Exception:
                self._finish(job, "failed", {"code": "WORKER_START_FAILED", "error": "Không khởi động được bộ xử lý thiết bị"})
                self._remove_files(job)
                job.released = True
                logging.warning("Imaging worker start failed")
            return job.snapshot()

    def snapshot(self, owner: str, job_id: str) -> dict:
        with self.lock:
            return self._owned(owner, job_id).snapshot()

    def cancel(self, owner: str, job_id: str) -> dict:
        with self.lock:
            job = self._owned(owner, job_id)
            if job.finished_at == 0:
                job.cancel_requested = True
                job.state = "cancelling"
            return job.snapshot()

    def cancel_request(self, owner: str, request_id: str) -> dict:
        with self.lock:
            for job in self.jobs.values():
                if job.owner == owner and job.request_id == request_id:
                    return self.cancel(owner, job.id)
            self._prune()
            if len(self.cancelled_requests) >= 256:
                raise AgentError(503, "CANCEL_LIMIT", "Quá nhiều yêu cầu hủy")
            self.cancelled_requests[(owner, request_id)] = time.monotonic()
            return {"ok": True, "state": "not_found"}

    def release(self, owner: str, job_id: str) -> dict:
        with self.lock:
            job = self._owned(owner, job_id)
            if not job.finished_at:
                raise AgentError(409, "BUSY", "Tác vụ chưa dừng")
            self._remove_files(job)
            job.released = True
            return {"ok": True}

    def open_image(self, owner: str, job_id: str, index: int):
        with self.lock:
            job = self._owned(owner, job_id)
            images = job.result.get("images", [])
            if job.state != "completed" or job.released or not 0 <= index < len(images):
                raise AgentError(404, "IMAGE_NOT_FOUND", "Ảnh không còn trong bộ nhớ tạm")
            path = job.directory / f"{index:03d}.jpg"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE:
                raise AgentError(404, "IMAGE_NOT_FOUND", "Không tìm thấy ảnh")
            return path.open("rb"), path.stat().st_size

    def revoke_pairings(self) -> None:
        with self.lock:
            self.pairings.forget_all()
            for job in self.jobs.values():
                if not job.finished_at:
                    job.cancel_requested = True
                    job.state = "cancelling"
                else:
                    self._remove_files(job)
                    job.released = True

    def clear_folder(self, grant: dict) -> dict:
        self.pairings.save_folder(grant["clinicKey"], "")
        return {"ok": True}

    def save_device(self, grant: dict, body: dict) -> dict:
        device_id, source = identifier(body.get("deviceId")), text(body.get("sourceName"))
        with self.lock:
            if source not in self.sources:
                raise AgentError(400, "SOURCE_NOT_FOUND", "Hãy tìm lại thiết bị và chọn một nguồn trong danh sách")
            return {"ok": True, **self.pairings.save_device(grant["clinicKey"], device_id, source)}

    def _owned(self, owner: str, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if not job or job.owner != owner:
            raise AgentError(404, "JOB_NOT_FOUND", "Không tìm thấy tác vụ")
        return job

    def _finish(self, job: Job, state: str, result: dict) -> None:
        job.state, job.result, job.finished_at = state, result, time.monotonic()
        if self.active == job.id:
            self.active = None
        logging.info("Imaging job finished: kind=%s state=%s", job.kind, state)

    def _disk_usage(self) -> int:
        return sum(file.stat().st_size for job in self.jobs.values() for file in job.directory.glob("*") if file.is_file())

    def _remove_files(self, job: Job) -> None:
        if time.monotonic() < job.cleanup_retry_at:
            return
        try:
            safe_remove_job(self.root, job.directory)
            job.released = True
        except OSError:
            job.cleanup_retry_at = time.monotonic() + 30
            logging.warning("Imaging temporary cleanup postponed")

    def _prune(self) -> None:
        now = time.monotonic()
        self.cancelled_requests = {key: stamp for key, stamp in self.cancelled_requests.items() if now - stamp < JOB_TTL_SECONDS}
        for job_id, job in list(self.jobs.items()):
            if job.finished_at and now - job.finished_at >= JOB_TTL_SECONDS:
                self._remove_files(job)
                if not job.directory.exists():
                    del self.jobs[job_id]

    def _monitor(self) -> None:
        while not self.stop_event.wait(0.1):
            try:
                self.tick()
            except Exception:
                logging.error("Imaging supervisor could not complete its check")

    def tick(self) -> None:
        with self.lock:
            self._prune()
            job = self.jobs.get(self.active) if self.active else None
            if not job:
                return
            must_stop = job.cancel_requested or time.monotonic() >= job.deadline
            process = job.process
            alive = process.alive() if process else False
            if not must_stop and alive:
                return
        # Never wait for a driver/process while holding the lock used by /health.
        if must_stop and process and not process.terminate():
            if not job.stop_warning:
                logging.error("Imaging worker has not stopped; device remains busy")
                job.stop_warning = True
            return
        if process:
            process.close()
        with self.lock:
            if job.finished_at:
                return
            if must_stop or job.cancel_requested or time.monotonic() >= job.deadline:
                self._finish(job, "cancelled" if job.cancel_requested else "timed_out",
                             {"code": "CANCELLED" if job.cancel_requested else "CAPTURE_TIMEOUT",
                              "error": "Đã hủy tác vụ" if job.cancel_requested else "Tác vụ quá thời gian chờ; bộ xử lý đã được dừng"})
                self._remove_files(job)
                return
            try:
                result = read_json(job.directory / "result.json")
                if job.kind == "sources" and result.get("ok"):
                    sources = result.get("sources", [])
                    if not isinstance(sources, list) or len(sources) > 128:
                        raise ValueError()
                    self.sources = [text(source) for source in sources]
                if job.kind == "folder" and result.get("ok"):
                    folder = text(result.get("folder"), 4096)
                    if not Path(folder).is_absolute() or folder.startswith(("\\\\", "//")):
                        raise ValueError("Invalid local folder")
                    self.pairings.save_folder(job.clinic_key, folder)
                if job.kind in IMAGE_KINDS and result.get("ok"):
                    self._validate_images(job, result)
                if result.get("ok"):
                    result.pop("ok", None)
                    self._finish(job, "completed", result)
                else:
                    result.pop("ok", None)
                    self._finish(job, "cancelled" if result.get("code") == "CANCELLED" else "failed", result)
                    self._remove_files(job)
            except (OSError, ValueError, AgentError):
                self._finish(job, "failed", {"code": "WORKER_CRASHED", "error": "Bộ xử lý thiết bị đã dừng bất thường"})
                self._remove_files(job)

    def _validate_images(self, job: Job, result: dict) -> None:
        images = result.get("images")
        if not isinstance(images, list) or len(images) > MAX_IMAGES:
            raise ValueError("Invalid images")
        total = 0
        for index, item in enumerate(images):
            path = job.directory / f"{index:03d}.jpg"
            if not isinstance(item, dict) or item.get("index") != index or path.is_symlink():
                raise ValueError("Invalid image")
            name = text(item.get("name"), 160)
            if "/" in name or "\\" in name or not name.endswith(".jpg"):
                raise ValueError("Invalid image name")
            size = path.stat().st_size
            if not 0 < size <= MAX_IMAGE or item.get("size") != size:
                raise ValueError("Invalid image size")
            total += size
        if total > MAX_JOB_BYTES:
            raise ValueError("Images too large")

    def close(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        with self.lock:
            active = self.jobs.get(self.active) if self.active else None
        if active and active.process:
            active.process.terminate()
        with self.lock:
            for job in self.jobs.values():
                if not job.process or not job.process.alive():
                    self._remove_files(job)
