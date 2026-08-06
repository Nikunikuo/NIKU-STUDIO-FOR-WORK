from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest

import psutil

from webui.process_guard import ProcessJob


@unittest.skipUnless(os.name == "nt", "Windows Job Objects are Windows-only")
class WindowsProcessJobTests(unittest.TestCase):
    def test_job_close_kills_orphan_after_parent_has_already_exited(self):
        parent_code = (
            "import subprocess,sys,time; "
            "time.sleep(0.3); "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(child.pid,flush=True)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        job = ProcessJob()
        child_pid: int | None = None
        try:
            job.attach(process)
            assert process.stdout is not None
            child_pid = int(process.stdout.readline().strip())
            process.wait(timeout=10)
            self.assertTrue(psutil.pid_exists(child_pid))
            job.terminate()
            deadline = time.monotonic() + 5
            while psutil.pid_exists(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(psutil.pid_exists(child_pid))
        finally:
            job.terminate()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if child_pid is not None and psutil.pid_exists(child_pid):
                try:
                    psutil.Process(child_pid).kill()
                except psutil.Error:
                    pass
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def test_terminate_is_idempotent(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        job = ProcessJob()
        try:
            job.attach(process)
            job.terminate()
            job.terminate()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            job.terminate()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
