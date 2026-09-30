"""Validate a built EXE without installing it or touching real patient/device data."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_common import atomic_json
from worker_process import WorkerProcess

parser = argparse.ArgumentParser()
parser.add_argument("--exe", required=True)
args = parser.parse_args()
exe = str(Path(args.exe).resolve())
with tempfile.TemporaryDirectory(prefix="vidental-packaged-test-") as temporary:
    root = Path(temporary)
    for name, params in (("capture", {"jpegQuality": 92}), ("cancel", {"jpegQuality": 92, "_testHang": True})):
        directory = root / name
        directory.mkdir()
        atomic_json(directory / "request.json", {"kind": "capture", "mock": True, "params": params})
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", exe):
            process = WorkerProcess(directory)
        try:
            if name == "cancel":
                time.sleep(0.5)
                assert process.alive()
                assert process.terminate()
                assert not process.alive()
            else:
                until = time.monotonic() + 20
                while process.alive() and time.monotonic() < until:
                    time.sleep(0.1)
                assert not process.alive(), "Frozen worker did not finish"
                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                assert result["ok"], result
                assert (directory / "000.jpg").stat().st_size == result["images"][0]["size"]
        finally:
            process.terminate()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([exe, "--mock", "--quiet", "--data-dir", str(root / "app"), "--port", str(port)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        until = time.monotonic() + 20
        health = None
        while time.monotonic() < until:
            assert process.poll() is None, "Frozen GUI app exited unexpectedly"
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    health = json.load(response)
                break
            except OSError:
                time.sleep(0.1)
        assert health and health["protocol"] == 2 and health["mock"], health
        time.sleep(1)
        assert process.poll() is None, "Frozen GUI/tray initialization failed"
        print("PASS: packaged worker capture + cancellation + GUI/tray startup + health", health["architecture"], "bit")
    finally:
        process.terminate()
        process.wait(timeout=5)
