from __future__ import annotations

import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

from agent_common import MAX_IMAGE, MAX_IMAGES, MAX_JOB_BYTES, MAX_PIXELS, atomic_json, read_json


class FrameWriter:
    def __init__(self, directory: Path, params: dict):
        self.directory = directory
        self.quality = params["jpegQuality"]
        self.prefix = re.sub(r"[^A-Za-z0-9_-]", "-", params.get("filePrefix", "anh"))[:80] or "anh"
        self.frames = []
        self.total = 0
        self.local_folder = params.get("localFolder", "")
        self.local_copy_failed = False

    def add(self, bmp_path: Path) -> None:
        from PIL import Image
        if len(self.frames) >= MAX_IMAGES:
            raise ValueError("IMAGE_LIMIT")
        if bmp_path.stat().st_size > MAX_PIXELS * 4 + 1024 * 1024:
            raise ValueError("IMAGE_LIMIT")
        index = len(self.frames)
        destination = self.directory / f"{index:03d}.jpg"
        with Image.open(bmp_path) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("IMAGE_LIMIT")
            with source.convert("RGB") as rgb:
                rgb.save(destination, format="JPEG", quality=self.quality, optimize=True)
        size = destination.stat().st_size
        if size > MAX_IMAGE or self.total + size > MAX_JOB_BYTES:
            destination.unlink(missing_ok=True)
            raise ValueError("IMAGE_LIMIT")
        self.total += size
        self.frames.append({"index": index, "name": f"{self.prefix}-{datetime.now():%Y%m%d-%H%M%S}-{index + 1}.jpg",
                            "size": size})
        bmp_path.unlink(missing_ok=True)
        if self.local_folder:
            temporary = Path(self.local_folder) / f".vidental-{secrets.token_hex(16)}.pending"
            target = temporary.with_name(f"{self.prefix}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(8)}.jpg")
            try:
                with temporary.open("xb") as output, destination.open("rb") as source:
                    shutil.copyfileobj(source, output, length=128 * 1024)
                os.rename(temporary, target)
            except OSError:
                self.local_copy_failed = True
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    self.local_copy_failed = True

    def result(self):
        return {"ok": True, "images": self.frames,
                "warning": "Không lưu được một số bản sao trên máy; ảnh vẫn được trả về web." if self.local_copy_failed else ""}


def mock_job(kind: str, directory: Path, params: dict) -> dict:
    from PIL import Image, ImageDraw
    if kind == "sources":
        return {"ok": True, "sources": ["ViDental Mock Sensor"]}
    if params.get("_testHang"):
        while True:
            time.sleep(1)
    if params.get("_testCrash"):
        os._exit(9)
    time.sleep(0.2)
    with Image.new("RGB", (1200, 900), "#152030") as picture:
        draw = ImageDraw.Draw(picture)
        for i in range(6):
            x = 110 + i * 175
            draw.rounded_rectangle((x, 220, x + 120, 610), 40, fill=(210 - i * 8,) * 3)
        draw.text((35, 35), "ViDental - MOCK / TEST IMAGE", fill="white")
        bmp = directory / "capture.bmp"
        picture.save(bmp)
    writer = FrameWriter(directory, params)
    writer.add(bmp)
    return writer.result()


def twain_job(kind: str, directory: Path, params: dict) -> dict:
    import tkinter as tk
    import twain

    root = tk.Tk()
    root.withdraw()
    try:
        with twain.SourceManager(root, ProductName="ViDental Imaging", Manufacturer="ViDental") as manager:
            if kind == "sources":
                value = manager.source_list
                return {"ok": True, "sources": list(value() if callable(value) else value)[:128]}
            source_name = params["sourceName"]
            with manager.open_source(source_name) as source:
                writer = FrameWriter(directory, params)

                def before(info):
                    width, height = info.get("ImageWidth", 0), info.get("ImageLength", 0)
                    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0 \
                            or width * height > MAX_PIXELS or len(writer.frames) >= MAX_IMAGES:
                        raise ValueError("IMAGE_LIMIT")

                def after(image, remaining):
                    bmp = directory / "capture.bmp"
                    try:
                        image.save(str(bmp))
                        writer.add(bmp)
                    finally:
                        image.close()
                        bmp.unlink(missing_ok=True)

                # pytwain 2.3: after(image, remaining), show_ui, modal; driver loop stays in this process.
                source.acquire_natively(after=after, before=before, show_ui=params["showUi"], modal=True)
                return writer.result()
    finally:
        root.destroy()


def choose_folder(params: dict) -> dict:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(parent=root, title=f"Thư mục lưu ảnh cục bộ — {params['label']}", mustexist=True)
        if not folder:
            return {"ok": False, "code": "CANCELLED", "error": "Đã hủy chọn thư mục"}
        if folder.startswith(("\\\\", "//")):
            raise ValueError("INVALID_FOLDER")
        path = Path(folder).resolve()
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
            if kernel.GetDriveTypeW(path.anchor) not in (2, 3):
                raise ValueError("INVALID_FOLDER")
        if not path.is_dir():
            raise ValueError("INVALID_FOLDER")
        return {"ok": True, "folder": str(path)}
    finally:
        root.destroy()


def run(directory: str) -> int:
    directory_path = Path(directory).resolve()
    # Parent assigns the child to a Windows Job Object before native code is allowed to start.
    deadline = time.monotonic() + 15
    while not (directory_path / "start").exists():
        if time.monotonic() >= deadline:
            return 2
        time.sleep(0.02)
    try:
        request = read_json(directory_path / "request.json")
        result = choose_folder(request["params"]) if request["kind"] == "folder" else (mock_job if request["mock"] else twain_job)(
                request["kind"], directory_path, request["params"])
    except Exception as error:
        # No raw driver messages, patient data, paths or tokens are sent to logs/web.
        code = str(error) if str(error) in {"IMAGE_LIMIT", "INVALID_FOLDER"} else "DRIVER_ERROR"
        messages = {"IMAGE_LIMIT": "Ảnh vượt giới hạn cho phép",
                    "INVALID_FOLDER": "Chọn thư mục trên ổ cứng hoặc USB của máy này",
                    "DRIVER_ERROR": "Không làm việc được với driver. Kiểm tra driver, thiết bị và bản 32/64-bit."}
        result = {"ok": False, "code": code, "error": messages[code]}
    atomic_json(directory_path / "result.json", result)
    return 0
