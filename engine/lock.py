"""Prevent CLI and web engines from competing for the same hardware."""
import os
from pathlib import Path


class EngineLock:
    def __init__(self, path=None):
        self.path = Path(path or Path.home() / '.cache/triputer/engine.lock')
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b' ')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('Another Triputer engine is running. Stop the web server or CLI interaction first.') from None
        self.file = handle

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
