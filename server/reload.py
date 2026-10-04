"""Development supervisor: a fresh interpreter, never importlib.reload."""
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def fingerprint(root=ROOT):
    return tuple((str(path.relative_to(root)), path.stat().st_mtime_ns, path.stat().st_size)
                 for folder in ('server', 'engine', 'controllers', 'interactions')
                 for path in sorted((root / folder).rglob('*'))
                 if path.is_file() and path.suffix in ('.py', '.html', '.css', '.js'))


def stop_child(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=45)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def supervise(arguments):
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    child = None
    try:
        previous = fingerprint()
        pending = None
        while True:
            if child is None:
                child = subprocess.Popen([sys.executable, '-m', 'server', *arguments], cwd=ROOT,
                                         start_new_session=True)
            time.sleep(.25)
            if (ROOT / '.uploading').exists():
                pending = None
                continue
            try:
                current = fingerprint()
            except OSError:
                # Editors may replace a file between directory listing and stat.
                pending = None
                continue
            if current != previous:
                previous, pending = current, time.monotonic()
            if pending is not None and time.monotonic() - pending >= .75:
                print('Code updated; restarting server and restoring assignments.', flush=True)
                stop_child(child)
                child, pending = None, None
            # A failed import stays stopped until the next edit, without a crash loop.
    except KeyboardInterrupt:
        pass
    finally:
        if child:
            stop_child(child)
    return 0
