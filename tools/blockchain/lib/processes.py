"""Child-process logging and shutdown for the independent observation tools."""
import os
from pathlib import Path
import signal
import subprocess
import time


class Processes:
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.children = []

    def start(self, name, command):
        handle = (self.log_dir / f'{name}.log').open('x')
        try:
            child = subprocess.Popen([str(x) for x in command], stdout=handle,
                stderr=subprocess.STDOUT, start_new_session=True)
        finally:
            handle.close()
        self.children.append((name, child))
        return child

    def require_alive(self):
        for name, child in self.children:
            if child.poll() is not None:
                raise RuntimeError(f'{name} exited ({child.returncode}); see {self.log_dir / (name + ".log")}')

    def stop(self):
        for _, child in self.children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 20
        for _, child in self.children:
            try:
                child.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


def interrupted(signum, frame):
    raise KeyboardInterrupt
