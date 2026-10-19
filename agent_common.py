from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

VERSION = "2.1.0"
PROTOCOL = 2
PORT = 27100
MAX_JSON = 64 * 1024
MAX_IMAGE = 20 * 1024 * 1024
MAX_JOB_BYTES = 50 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_IMAGES = 50
CAPTURE_SECONDS = 600
JOB_TTL_SECONDS = 900
DEFAULT_ORIGIN = "https://app.vidental.vn"


class AgentError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path, maximum: int = MAX_JSON) -> dict:
    if path.stat().st_size > maximum:
        raise ValueError("JSON too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def text(value: object, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise AgentError(400, "INVALID_INPUT", "Thông tin gửi lên không hợp lệ")
    return value.strip()


def identifier(value: object) -> str:
    value = text(value, 128)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise AgentError(400, "INVALID_INPUT", "Mã định danh không hợp lệ")
    return value


def safe_remove_job(root: Path, directory: Path) -> None:
    import shutil
    root, directory = root.resolve(), directory.resolve()
    if directory.parent != root or not re.fullmatch(r"[a-f0-9]{32}", directory.name):
        raise ValueError("Refuse cleanup outside owned job directory")
    if directory.is_dir():
        shutil.rmtree(directory)


class Pairings:
    def __init__(self, path: Path):
        import threading
        self.path = path
        self.lock = threading.RLock()
        self.data = {"grants": {}, "machines": {}}
        if path.exists():
            self.data = read_json(path, 1024 * 1024)
            if not isinstance(self.data.get("grants"), dict) or not isinstance(self.data.get("machines"), dict):
                raise ValueError("Invalid saved settings")
        self.sessions = {}
        self.code = None
        self.code_until = 0.0
        self.attempts = 0

    def new_code(self) -> str:
        with self.lock:
            self.code = "".join(secrets.choice("0123456789") for _ in range(8))
            self.code_until = time.monotonic() + 120
            self.attempts = 0
            return self.code

    def pair(self, code: str, clinic_key: str, label: str, origin: str) -> dict:
        with self.lock:
            if self.attempts >= 5 or time.monotonic() >= self.code_until or self.code is None:
                raise AgentError(403, "PAIRING_EXPIRED", "Hãy mở bộ kết nối trên máy và tạo mã mới")
            self.attempts += 1
            if not secrets.compare_digest(code, self.code):
                raise AgentError(403, "PAIRING_INVALID", "Mã kết nối không đúng")
            self.code = None
            now = time.time()
            grants = self.data["grants"]
            for grant_id in list(grants):
                if grants[grant_id]["expiresAt"] <= now:
                    del grants[grant_id]
            if len(grants) >= 32 or (clinic_key not in self.data["machines"] and len(self.data["machines"]) >= 64):
                raise AgentError(409, "PAIRING_LIMIT", "Xóa kết nối cũ trong bộ kết nối trên máy trước")
            token = secrets.token_urlsafe(32)
            grant_id = secrets.token_hex(16)
            grants[grant_id] = {"hash": digest(token), "clinicKey": clinic_key, "label": label,
                                "origin": origin, "expiresAt": now + 30 * 86400}
            atomic_json(self.path, self.data)
            return {"ok": True, "pairToken": token, "expiresAt": grants[grant_id]["expiresAt"]}

    def open_session(self, token: str, origin: str) -> dict:
        with self.lock:
            now = time.time()
            self.sessions = {key: value for key, value in self.sessions.items() if value["expiresAt"] > now}
            for grant_id, grant in self.data["grants"].items():
                if grant["origin"] == origin and grant["expiresAt"] > now and secrets.compare_digest(grant["hash"], digest(token)):
                    # A browser reconnect must not grow session state indefinitely.
                    existing = [key for key, value in self.sessions.items() if value["grantId"] == grant_id]
                    if len(existing) >= 8:
                        self.sessions.pop(existing[0], None)
                    secret = secrets.token_urlsafe(32)
                    expiry = min(now + 3600, grant["expiresAt"])
                    self.sessions[digest(secret)] = {"grantId": grant_id, "expiresAt": expiry}
                    return {"ok": True, "sessionToken": secret, "expiresAt": expiry}
            raise AgentError(401, "PAIRING_REQUIRED", "Cần kết nối lại với bộ kết nối trên máy này")

    def authorize(self, token: str, origin: str) -> tuple[str, dict]:
        with self.lock:
            session = self.sessions.get(digest(token))
            grant = self.data["grants"].get(session["grantId"]) if session else None
            if (not session or session["expiresAt"] <= time.time() or not grant
                    or grant["expiresAt"] <= time.time() or grant["origin"] != origin):
                raise AgentError(401, "PAIRING_REQUIRED", "Phiên kết nối đã hết hạn")
            return session["grantId"], dict(grant)

    def settings(self, clinic_key: str) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.data["machines"].get(clinic_key, {"devices": {}, "folder": ""})))

    def save_device(self, clinic_key: str, device_id: str, source: str) -> dict:
        with self.lock:
            machine = self.data["machines"].setdefault(clinic_key, {"devices": {}, "folder": ""})
            if len(machine["devices"]) >= 64 and device_id not in machine["devices"]:
                raise AgentError(409, "DEVICE_LIMIT", "Máy này đã đạt giới hạn cấu hình thiết bị")
            machine["devices"][device_id] = source
            atomic_json(self.path, self.data)
            return self.settings(clinic_key)

    def save_folder(self, clinic_key: str, folder: str) -> None:
        # Called only after the native local folder picker, never with an arbitrary HTTP path.
        with self.lock:
            machine = self.data["machines"].setdefault(clinic_key, {"devices": {}, "folder": ""})
            machine["folder"] = folder
            atomic_json(self.path, self.data)

    def forget_all(self) -> None:
        with self.lock:
            self.data["grants"] = {}
            self.sessions.clear()
            self.code = None
            atomic_json(self.path, self.data)
