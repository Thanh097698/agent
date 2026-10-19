from __future__ import annotations

import json
import logging
import re
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from agent_common import AgentError, MAX_JSON, PROTOCOL, VERSION, identifier, text


class AgentServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    allow_reuse_address = False

    def __init__(self, address: tuple, origins: set[str], request_seconds: float = 8):
        if address[0] != "127.0.0.1":
            raise ValueError("Imaging agent must bind to 127.0.0.1")
        self.runtime = None
        self.origins = origins
        self.request_seconds = request_seconds
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        logging.warning("Imaging HTTP request failed")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "ViDentalImagingAgent/" + VERSION
    sys_version = ""

    def setup(self):
        self.request.settimeout(self.server.request_seconds)
        self.deadline_timer = threading.Timer(self.server.request_seconds, self._expire)
        self.deadline_timer.daemon = True
        self.deadline_timer.start()
        super().setup()

    def finish(self):
        try:
            super().finish()
        except OSError:
            pass
        finally:
            self.deadline_timer.cancel()

    def _expire(self):
        try:
            self.request.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def log_message(self, fmt, *args):
        # Never log URL/query, request bodies, pairing codes, authorization or local paths.
        pass

    def _cors(self):
        origin = self.headers.get("Origin")
        self.send_header("Vary", "Origin")
        if origin in self.server.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")

    def _json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, public_health=False):
        port = self.server.server_address[1]
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise AgentError(403, "HOST_DENIED", "Địa chỉ bộ kết nối không hợp lệ")
        origin = self.headers.get("Origin")
        if origin not in self.server.origins and not (public_health and origin is None):
            raise AgentError(403, "ORIGIN_DENIED", "Trang này không được phép gọi bộ kết nối")
        if len(self.headers.get_all("Host", [])) != 1 or len(self.headers.get_all("Origin", [])) > 1:
            raise AgentError(400, "INVALID_HEADERS", "Header không hợp lệ")
        return origin

    def _body(self):
        if self.headers.get("Transfer-Encoding") is not None:
            raise AgentError(400, "INVALID_BODY", "Không hỗ trợ transfer encoding")
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1:
            raise AgentError(411, "LENGTH_REQUIRED", "Cần Content-Length")
        if not re.fullmatch(r"[0-9]{1,9}", values[0]):
            raise AgentError(400, "INVALID_BODY", "Kích thước body không hợp lệ")
        size = int(values[0])
        if not 1 <= size <= MAX_JSON:
            raise AgentError(413, "BODY_TOO_LARGE", "Body vượt giới hạn cho phép")
        if self.headers.get_content_type() != "application/json":
            raise AgentError(415, "JSON_REQUIRED", "Cần body JSON")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise AgentError(400, "INVALID_BODY", "Body chưa được gửi đầy đủ")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise AgentError(400, "INVALID_JSON", "JSON không hợp lệ") from None
        if not isinstance(value, dict):
            raise AgentError(400, "INVALID_JSON", "JSON phải là object")
        return value

    def _token(self):
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise AgentError(401, "PAIRING_REQUIRED", "Cần kết nối với bộ kết nối trên máy này")
        return text(values[0][7:], 128)

    def _route(self):
        runtime = self.server.runtime
        if runtime is None:
            raise AgentError(503, "STARTING", "Bộ kết nối đang khởi động")
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or parsed.scheme or parsed.netloc:
            raise AgentError(400, "INVALID_PATH", "Đường dẫn không hợp lệ")
        path = parsed.path
        origin = self._guard(public_health=self.command == "GET" and path == "/health")
        if self.command == "OPTIONS":
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.command == "GET" and path == "/health":
            self._json(200, {"ok": True, "version": VERSION, "protocol": PROTOCOL, "mock": runtime.mock,
                             "busy": runtime.busy(), "architecture": struct.calcsize("P") * 8,
                             "fingerprint": runtime.reader.present()})
            return
        # Parse bounded JSON before acquiring any job/device state.
        body = self._body() if self.command == "POST" else {}
        if self.command == "POST" and path == "/pair":
            value = runtime.pairings.pair(text(body.get("code"), 8), identifier(body.get("clinicKey")),
                                          text(body.get("label"), 120), origin)
            self._json(200, value)
            return
        token = self._token()
        if self.command == "POST" and path == "/session":
            self._json(200, runtime.pairings.open_session(token, origin))
            return
        owner, grant = runtime.pairings.authorize(token, origin)
        if self.command == "GET" and path == "/settings":
            self._json(200, {"ok": True, **runtime.pairings.settings(grant["clinicKey"])})
        elif self.command == "POST" and path == "/settings/device":
            self._json(200, runtime.save_device(grant, body))
        elif self.command == "POST" and path == "/settings/clear-folder":
            self._json(200, runtime.clear_folder(grant))
        elif self.command == "POST" and path == "/jobs":
            self._json(202, runtime.start_job(owner, grant, body))
        elif self.command == "POST" and path == "/jobs/cancel-request":
            self._json(200, runtime.cancel_request(owner, identifier(body.get("requestId"))))
        elif re.fullmatch(r"/jobs/[a-f0-9]{32}", path):
            job_id = path.split("/")[2]
            if self.command == "GET":
                self._json(200, runtime.snapshot(owner, job_id))
            elif self.command == "DELETE":
                self._json(200, runtime.release(owner, job_id))
            else:
                raise AgentError(405, "METHOD_NOT_ALLOWED", "Phương thức không được hỗ trợ")
        elif self.command == "POST" and re.fullmatch(r"/jobs/[a-f0-9]{32}/cancel", path):
            self._json(200, runtime.cancel(owner, path.split("/")[2]))
        elif self.command == "GET" and re.fullmatch(r"/jobs/[a-f0-9]{32}/images/[0-9]{1,2}", path):
            parts = path.split("/")
            file, size = runtime.open_image(owner, parts[2], int(parts[4]))
            with file:
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                while chunk := file.read(128 * 1024):
                    self.wfile.write(chunk)
        elif path in {"/capture", "/cancel", "/sources"}:
            raise AgentError(410, "UPGRADE_REQUIRED", "Cần cập nhật web và bộ kết nối lên giao thức mới")
        else:
            raise AgentError(404, "NOT_FOUND", "Không tìm thấy chức năng")

    def _handle(self):
        try:
            self._route()
        except AgentError as error:
            self._json(error.status, {"ok": False, "code": error.code, "error": str(error)})
        except (OSError, TimeoutError):
            pass
        except Exception:
            logging.error("Imaging request could not be processed")
            try:
                self._json(500, {"ok": False, "code": "INTERNAL_ERROR", "error": "Không xử lý được yêu cầu"})
            except OSError:
                pass

    do_GET = do_POST = do_DELETE = do_OPTIONS = _handle
