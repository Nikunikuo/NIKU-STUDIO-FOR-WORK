"""Windows process-tree ownership for local H3 subprocesses.

`psutil` can enumerate descendants only while their parent still exists.  A
Windows Job Object with KILL_ON_JOB_CLOSE keeps ownership in the kernel, so a
helper that outlives an unexpectedly exited parent is still reclaimed.
"""

from __future__ import annotations

import os
import threading
from subprocess import Popen
from typing import Any

import psutil


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    _kernel32.IsProcessInJob.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class ProcessJob:
    """Own one Popen tree; on Windows, closing it kills every assigned member."""

    def __init__(self) -> None:
        self._handle: Any | None = None
        self._root_pid: int | None = None
        self._assigned: set[int] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._watch_error: BaseException | None = None
        if os.name == "nt":
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            information = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(information),
                ctypes.sizeof(information),
            ):
                error = ctypes.get_last_error()
                _kernel32.CloseHandle(handle)
                raise ctypes.WinError(error)
            self._handle = handle

    @property
    def supported(self) -> bool:
        return self._handle is not None

    def attach(self, process: Popen[Any]) -> None:
        """Assign the root immediately, then adopt any redirector descendants."""

        if self._handle is None:
            return
        with self._lock:
            if self._root_pid is not None:
                raise RuntimeError("this ProcessJob already owns a process")
            self._root_pid = int(process.pid)
            self._assign_pid(self._root_pid, strict=True)
            self._refresh(strict=True)
            self._watcher = threading.Thread(
                target=self._watch,
                name=f"h3-process-job-{self._root_pid}",
                daemon=True,
            )
            self._watcher.start()

    def check(self) -> None:
        with self._lock:
            error = self._watch_error
        if error is not None:
            raise RuntimeError(f"failed to retain subprocess ownership: {error}") from error

    def _assign_pid(self, pid: int, *, strict: bool) -> None:
        if self._handle is None or pid in self._assigned:
            return
        process_handle = _kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not process_handle:
            if strict:
                raise ctypes.WinError(ctypes.get_last_error())
            return
        try:
            if not _kernel32.AssignProcessToJobObject(self._handle, process_handle):
                error = ctypes.get_last_error()
                in_this_job = wintypes.BOOL()
                if not _kernel32.IsProcessInJob(process_handle, self._handle, ctypes.byref(in_this_job)):
                    if strict:
                        raise ctypes.WinError(ctypes.get_last_error())
                    return
                if not in_this_job.value:
                    if strict:
                        raise ctypes.WinError(error)
                    return
            self._assigned.add(pid)
        finally:
            _kernel32.CloseHandle(process_handle)

    def _refresh(self, *, strict: bool) -> None:
        if self._root_pid is None or self._handle is None:
            return
        try:
            root = psutil.Process(self._root_pid)
            descendants = root.children(recursive=True)
        except psutil.Error:
            return
        for child in descendants:
            self._assign_pid(child.pid, strict=strict)

    def _watch(self) -> None:
        while not self._stop.wait(0.05):
            try:
                with self._lock:
                    # Later descendants inherit the Job Object from their
                    # assigned parent. This pass mainly adopts Windows venv
                    # redirector children that already existed at attach time.
                    self._refresh(strict=False)
            except BaseException as exc:  # surfaced synchronously by check()
                with self._lock:
                    self._watch_error = exc
                return

    def terminate(self) -> None:
        """Terminate all members and close the kernel handle; safe to repeat."""

        self._stop.set()
        watcher = self._watcher
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=1)
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            # TerminateJobObject is immediate; KILL_ON_JOB_CLOSE remains the
            # final guarantee if a member races or the explicit call fails.
            _kernel32.TerminateJobObject(handle, 1)
            _kernel32.CloseHandle(handle)

    close = terminate

    def __enter__(self) -> "ProcessJob":
        return self

    def __exit__(self, *_: object) -> None:
        self.terminate()

    def __del__(self) -> None:
        try:
            self.terminate()
        except Exception:
            pass
