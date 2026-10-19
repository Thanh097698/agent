"""Máy điểm chỉ ZKTeco (ZK9500 và đầu đọc cùng dòng dùng ZKFinger SDK / libzkfp.dll).

- Dò máy cắm: liệt kê thiết bị USB đang có mặt qua SetupAPI của Windows (VID_1B55 = ZKTeco),
  KHÔNG nạp driver vào tiến trình chính.
- Lấy ảnh: chạy trong tiến trình con như TWAIN. Chỉ lấy ẢNH (AcquireFingerprintImage), không
  trích/lưu mẫu vân tay; ảnh xám được cắt sát ngón rồi trả về web như một ảnh JPEG.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from agent_common import MAX_IMAGE

ZKTECO_VID = "\\VID_1B55&"
FINGER_SECONDS = 30
PRESENCE_TTL = 2.0

# Mã lỗi của libzkfp.dll (libzkfperrdef.h)
ZKFP_ERR_ALREADY_INIT = 1
ZKFP_ERR_NO_DEVICE = -3
ZKFP_ERR_CAPTURE = -8  # chưa có ngón tay trên mặt kính

MESSAGES = {
    "NO_READER": "Không thấy máy điểm chỉ. Kiểm tra dây USB rồi thử lại.",
    "READER_DRIVER": "Chưa cài driver máy điểm chỉ ZKTeco (ZKFinger) hoặc driver khác bản 32/64-bit với bộ kết nối.",
    "READER_OPEN": "Không mở được máy điểm chỉ. Đóng phần mềm khác đang dùng máy rồi thử lại.",
    "READER_ERROR": "Máy điểm chỉ báo lỗi khi lấy ảnh. Rút ra cắm lại rồi thử lại.",
    "NO_FINGER": "Chưa thấy ngón tay trên máy điểm chỉ.",
}


class FingerprintError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def result(self) -> dict:
        return {"ok": False, "code": self.code, "error": MESSAGES[self.code]}


def _usb_instance_ids() -> list[str]:
    import ctypes
    from ctypes import wintypes

    class DevInfoData(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("classGuid", ctypes.c_ubyte * 16),
                    ("devInst", wintypes.DWORD), ("reserved", ctypes.c_size_t)]

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DevInfoData)]
    setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.POINTER(DevInfoData), wintypes.LPWSTR,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    # DIGCF_PRESENT | DIGCF_ALLCLASSES: chỉ thiết bị đang cắm, mọi lớp thiết bị dưới bus USB.
    handle = setupapi.SetupDiGetClassDevsW(None, "USB", None, 0x2 | 0x4)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        found = []
        info = DevInfoData()
        info.cbSize = ctypes.sizeof(DevInfoData)
        buffer = ctypes.create_unicode_buffer(512)
        index = 0
        while index < 4096 and setupapi.SetupDiEnumDeviceInfo(handle, index, ctypes.byref(info)):
            if setupapi.SetupDiGetDeviceInstanceIdW(handle, ctypes.byref(info), buffer, len(buffer), None):
                found.append(buffer.value)
            index += 1
        return found
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(handle)


class ReaderPresence:
    """Có máy điểm chỉ ZKTeco đang cắm không — /health gọi liên tục nên giữ kết quả 2 giây."""

    def __init__(self, mock: bool = False, lister=None):
        self.mock = mock
        self.lister = lister or _usb_instance_ids
        self.lock = threading.Lock()
        self.value = False
        self.checked = float("-inf")
        self.warned = False

    def present(self) -> bool:
        if self.mock:
            return True
        if os.name != "nt" and self.lister is _usb_instance_ids:
            return False
        with self.lock:
            if time.monotonic() - self.checked < PRESENCE_TTL:
                return self.value
            try:
                self.value = any(ZKTECO_VID in item.upper() for item in self.lister())
            except Exception:
                self.value = False
                if not self.warned:
                    logging.warning("Could not list USB devices for the fingerprint reader")
                    self.warned = True
            self.checked = time.monotonic()
            return self.value


def _load_library():
    """libzkfp.dll do driver ZKFinger cài vào thư mục hệ thống; ctypes (Python 3.8+) không tìm theo PATH
    nên thử thêm từng thư mục trong PATH bằng đường dẫn đầy đủ."""
    import ctypes
    try:
        return ctypes.WinDLL("libzkfp.dll")
    except OSError:
        pass
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder.strip('"')) / "libzkfp.dll" if folder.strip() else None
        if candidate and candidate.is_absolute() and candidate.is_file():
            try:
                return ctypes.WinDLL(str(candidate))
            except OSError:
                continue
    raise FingerprintError("READER_DRIVER")


def _read_reader(seconds: float) -> tuple[bytes, int, int]:
    import ctypes

    library = _load_library()
    handle_type, data = ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte)
    library.ZKFPM_Init.restype = ctypes.c_int
    library.ZKFPM_Terminate.restype = ctypes.c_int
    library.ZKFPM_GetDeviceCount.restype = ctypes.c_int
    library.ZKFPM_OpenDevice.argtypes = [ctypes.c_int]
    library.ZKFPM_OpenDevice.restype = handle_type
    library.ZKFPM_CloseDevice.argtypes = [handle_type]
    library.ZKFPM_CloseDevice.restype = ctypes.c_int
    library.ZKFPM_GetParameters.argtypes = [handle_type, ctypes.c_int, data, ctypes.POINTER(ctypes.c_uint)]
    library.ZKFPM_GetParameters.restype = ctypes.c_int
    library.ZKFPM_AcquireFingerprintImage.argtypes = [handle_type, data, ctypes.c_uint]
    library.ZKFPM_AcquireFingerprintImage.restype = ctypes.c_int

    def parameter(handle, code: int) -> int:
        value, size = (ctypes.c_ubyte * 4)(), ctypes.c_uint(4)
        if library.ZKFPM_GetParameters(handle, code, value, ctypes.byref(size)) != 0:
            raise FingerprintError("READER_ERROR")
        return int.from_bytes(bytes(value), "little")

    started = library.ZKFPM_Init()
    if started not in (0, ZKFP_ERR_ALREADY_INIT):
        raise FingerprintError("NO_READER" if started == ZKFP_ERR_NO_DEVICE else "READER_DRIVER")
    try:
        if library.ZKFPM_GetDeviceCount() <= 0:
            raise FingerprintError("NO_READER")
        handle = library.ZKFPM_OpenDevice(0)
        if not handle:
            raise FingerprintError("READER_OPEN")
        try:
            width, height = parameter(handle, 1), parameter(handle, 2)
            if not (64 <= width <= 2048 and 64 <= height <= 2048):
                raise FingerprintError("READER_ERROR")
            image = (ctypes.c_ubyte * (width * height))()
            deadline = time.monotonic() + seconds
            while True:
                code = library.ZKFPM_AcquireFingerprintImage(handle, image, width * height)
                if code == 0:
                    raw = bytes(image)
                    if finger_box(raw, width, height):
                        return raw, width, height
                elif code != ZKFP_ERR_CAPTURE:
                    raise FingerprintError("READER_ERROR")
                if time.monotonic() >= deadline:
                    raise FingerprintError("NO_FINGER")
                time.sleep(0.1)
        finally:
            library.ZKFPM_CloseDevice(handle)
    finally:
        library.ZKFPM_Terminate()


def _paper(raw: bytes, width: int, height: int):
    """Ảnh xám, vân tối trên nền sáng (đảo lại nếu cảm biến trả nền tối) + độ sáng của nền."""
    from PIL import Image, ImageOps
    picture = Image.frombytes("L", (width, height), raw)
    edge = []
    for box in ((0, 0, width, 4), (0, height - 4, width, height), (0, 0, 4, height), (width - 4, 0, width, height)):
        edge.extend(picture.crop(box).getdata())
    background = sorted(edge)[len(edge) // 2]
    if background < 128:
        picture, background = ImageOps.invert(picture), 255 - background
    return picture, background


def finger_box(raw: bytes, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Khung bao vùng ngón tay; None khi mặt kính trống (vùng vân quá ít)."""
    from PIL import ImageFilter
    picture, background = _paper(raw, width, height)
    limit = max(1, background - 40)
    if sum(picture.histogram()[:limit]) < width * height * 0.06:
        return None
    mask = picture.point(lambda value: 255 if value < limit else 0).filter(ImageFilter.MaxFilter(9))
    return mask.getbbox()


def render_print(raw: bytes, width: int, height: int):
    """Cắt sát ngón (chừa lề 6px), kéo tương phản — vân đen trên nền trắng, giữ nguyên 500 dpi."""
    from PIL import ImageOps
    box = finger_box(raw, width, height)
    if not box:
        raise FingerprintError("NO_FINGER")
    picture, _ = _paper(raw, width, height)
    left, top, right, bottom = box
    box = (max(0, left - 6), max(0, top - 6), min(width, right + 6), min(height, bottom + 6))
    return ImageOps.autocontrast(picture.crop(box), cutoff=1)


def mock_reader() -> tuple[bytes, int, int]:
    """Vân tay giả (xoáy ốc trong hình bầu dục) cỡ ảnh ZK9500 300×375 — chỉ cho chế độ giả lập."""
    import math
    width, height = 300, 375
    center_x, center_y = 150, 195
    pixels = bytearray(b"\xf2" * (width * height))
    for y in range(height):
        for x in range(width):
            dx, dy = (x - center_x) / 108, (y - center_y) / 150
            outside = dx * dx + dy * dy
            if outside >= 1:
                continue
            ring = math.hypot(x - center_x, (y - center_y) * 0.8 + 0.0035 * (x - center_x) ** 2)
            ridge = 0.5 + 0.5 * math.sin(ring / 2.4 + math.atan2(y - center_y, x - center_x))
            pixels[y * width + x] = 242 - int(205 * ridge * min(1.0, (1 - outside) * 5))
    return bytes(pixels), width, height


def capture_job(directory: Path, params: dict, mock: bool) -> dict:
    if mock:
        time.sleep(0.2)
        raw, width, height = mock_reader()
    else:
        raw, width, height = _read_reader(params.get("seconds", FINGER_SECONDS))
    destination = directory / "000.jpg"
    with render_print(raw, width, height) as picture:
        picture.save(destination, format="JPEG", quality=92)
    size = destination.stat().st_size
    if size > MAX_IMAGE:
        destination.unlink(missing_ok=True)
        raise ValueError("IMAGE_LIMIT")
    return {"ok": True, "images": [{"index": 0, "name": f"diem-chi-{datetime.now():%Y%m%d-%H%M%S}.jpg", "size": size}]}
