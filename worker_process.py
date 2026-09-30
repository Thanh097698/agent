from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


class WorkerProcess:
    def __init__(self, directory: Path):
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).with_name("agent.py")))
        command.extend(["--worker", str(directory)])
        self.job_handle = None
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, close_fds=True,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                        start_new_session=os.name != "nt")
        try:
            if os.name == "nt":
                self._assign_job()
            (directory / "start").touch()
        except Exception:
            self.process.kill()
            self.process.wait(timeout=3)
            self.close()
            raise

    def _assign_job(self) -> None:
        class BasicLimits(ctypes.Structure):
            _fields_ = [("perProcess", ctypes.c_longlong), ("perJob", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minWorkingSet", ctypes.c_size_t),
                        ("maxWorkingSet", ctypes.c_size_t), ("activeProcesses", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("readOps", "writeOps", "otherOps", "readBytes", "writeBytes", "otherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("processMemory", ctypes.c_size_t), ("jobMemory", ctypes.c_size_t),
                        ("peakProcess", ctypes.c_size_t), ("peakJob", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.job_handle = self.kernel.CreateJobObjectW(None, None)
        if not self.job_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000 | 0x100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
        limits.processMemory = 768 * 1024 * 1024
        if not self.kernel.SetInformationJobObject(self.job_handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.AssignProcessToJobObject(self.job_handle, wintypes.HANDLE(self.process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def alive(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> bool:
        if os.name == "nt" and self.job_handle:
            self.kernel.TerminateJobObject(self.job_handle, 1)
        elif self.alive():
            os.killpg(self.process.pid, signal.SIGKILL)
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return False
        self.close()
        return True

    def close(self) -> None:
        if self.job_handle:
            self.kernel.CloseHandle(self.job_handle)
            self.job_handle = None
