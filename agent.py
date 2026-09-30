from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import urlsplit

from agent_common import DEFAULT_ORIGIN, PORT, VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description="ViDental Imaging Agent")
    parser.add_argument("--worker", metavar="JOB_DIRECTORY", help=argparse.SUPPRESS)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--headless", action="store_true", help="For automated tests / supervised development only")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--allowed-origins", default=DEFAULT_ORIGIN)
    args = parser.parse_args()
    if args.worker:
        from twain_worker import run
        return run(args.worker)

    origins = set(args.allowed_origins.split(","))
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password \
                or parsed.path or parsed.query or parsed.fragment \
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}):
            parser.error("Allowed origins must be exact HTTPS origins (or explicit HTTP localhost for development)")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid port")

    from agent_server import AgentServer
    from agent_runtime import AgentRuntime
    data_dir = args.data_dir or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ViDental" / "ImagingAgent"
    data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(data_dir / "agent.log", maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        server = AgentServer(("127.0.0.1", args.port), origins)
    except OSError:
        if not args.headless:
            from tkinter import messagebox
            messagebox.showinfo("ViDental Imaging Agent", "Bộ kết nối đã chạy hoặc cổng đang được sử dụng. Mở biểu tượng ViDental cạnh đồng hồ Windows.")
        return 1
    try:
        runtime = AgentRuntime(data_dir, mock=args.mock)
        server.runtime = runtime
    except Exception:
        server.server_close()
        logging.error("Imaging settings could not be loaded; existing settings were preserved")
        if not args.headless:
            from tkinter import messagebox
            messagebox.showerror("ViDental Imaging Agent", "Không đọc được cấu hình bộ kết nối. Liên hệ hỗ trợ; cấu hình cũ chưa bị thay thế.")
        return 1
    runtime.start_monitor()
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="imaging-http")
    thread.start()
    logging.info("Imaging agent started: version=%s mock=%s", VERSION, args.mock)
    try:
        if args.headless:
            stopped = threading.Event()
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            stopped.wait()
        else:
            desktop(runtime, quiet=args.quiet)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        runtime.close()
        logging.info("Imaging agent stopped")
    return 0


def desktop(runtime, quiet: bool) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk
    from PIL import Image, ImageDraw
    import pystray

    root = tk.Tk()
    root.title("ViDental Imaging Agent")
    root.geometry("530x385")
    root.resizable(False, False)
    commands = queue.Queue()
    code = tk.StringVar(value="Bấm Tạo mã kết nối")
    status = tk.StringVar()
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Kết nối máy chụp với ViDental", font=("Segoe UI", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Trên web: Cấu hình → Tích hợp → Thiết bị hình ảnh → Thiết lập máy này.",
              wraplength=470).pack(anchor="w", pady=(12, 8))
    ttk.Label(frame, textvariable=code, font=("Consolas", 22, "bold")).pack(pady=8)

    def new_code():
        code.set(runtime.pairings.new_code())

    ttk.Button(frame, text="Tạo mã kết nối (hiệu lực 2 phút)", command=new_code).pack()
    ttk.Label(frame, text="Chỉ nhập mã trên web phòng khám bạn đang sử dụng.\nKhông gửi mã này cho người khác.",
              justify="center").pack(pady=10)
    ttk.Label(frame, textvariable=status, wraplength=470).pack(pady=6)

    def forget():
        if messagebox.askyesno("Thu hồi kết nối", "Ngắt tất cả web đã kết nối trên máy này và hủy lượt chụp đang chạy?", parent=root):
            runtime.revoke_pairings()
            code.set("Đã ngắt kết nối")

    ttk.Button(frame, text="Ngắt tất cả kết nối trên máy này", command=forget).pack(pady=8)
    ttk.Label(frame, text="Đóng cửa sổ để tiếp tục chạy dưới khay hệ thống.", foreground="#586174").pack()
    root.protocol("WM_DELETE_WINDOW", root.withdraw)
    icon_image = Image.new("RGB", (64, 64), "#2563eb")
    draw = ImageDraw.Draw(icon_image)
    draw.ellipse((12, 12, 52, 52), outline="white", width=5)
    draw.ellipse((25, 25, 39, 39), fill="white")
    icon = pystray.Icon("ViDentalImagingAgent", icon_image, "ViDental Imaging Agent",
                        menu=pystray.Menu(
                            pystray.MenuItem("Mở bộ kết nối", lambda *_: commands.put("show"), default=True),
                            pystray.MenuItem("Thoát", lambda *_: commands.put("exit"))))
    tray_thread = threading.Thread(target=icon.run, daemon=True, name="imaging-tray")
    tray_thread.start()

    def show():
        root.deiconify()
        root.lift()
        root.focus_force()

    def poll():
        while not commands.empty():
            command = commands.get_nowait()
            if command == "exit":
                if runtime.busy() and not messagebox.askyesno("Thoát", "Hủy lượt chụp đang chạy và thoát?", parent=root):
                    continue
                root.destroy()
                return
            show()
        status.set(f"v{VERSION} · {'GIẢ LẬP — không phải ảnh bệnh nhân' if runtime.mock else 'Sẵn sàng'}"
                   f" · {'Đang xử lý thiết bị' if runtime.busy() else 'Chưa có tác vụ'}")
        if runtime.pairings.code is None or time.monotonic() >= runtime.pairings.code_until:
            if code.get().isdigit():
                code.set("Tạo mã mới để kết nối")
        root.after(200, poll)

    if quiet:
        root.withdraw()
    root.after(200, poll)
    try:
        root.mainloop()
    finally:
        icon.stop()
        tray_thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
