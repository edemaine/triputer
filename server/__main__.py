"""Run the web launcher; --reload restarts this process on complete code updates."""
import argparse
from pathlib import Path
import signal
import sys

from engine import Engine
from engine.devices import DemoBackend
from .app import make_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=3333)
    parser.add_argument('--reload', action='store_true')
    parser.add_argument('--demo', action='store_true', help='Use three simulated controllers; no Bluetooth required')
    parser.add_argument('--state', type=Path, help='Assignment state file (default: ~/.local/state/triputer/web.json)')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    if args.reload:
        from .reload import supervise
        return supervise([arg for arg in sys.argv[1:] if arg != '--reload'])
    state = args.state or Path.home() / '.local/state/triputer' / ('demo.json' if args.demo else 'web.json')
    engine = Engine(backend=DemoBackend() if args.demo else None, state_path=state)
    http = None
    def stop(signum, frame):
        raise KeyboardInterrupt
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    try:
        http = make_server(engine, args.host, args.port, args.demo)
        engine.start()
        print(f'Triputer listening on http://{args.host}:{http.server_port}', flush=True)
        http.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f'Error: {error}', file=sys.stderr, flush=True)
        return 1
    finally:
        if http:
            http.server_close()
        engine.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
