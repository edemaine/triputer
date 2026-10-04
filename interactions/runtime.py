"""CLI wrappers around the shared engine; games remain usable as direct scripts."""
import math
import re
import signal
import sys
import time

from controllers.jpmini import BANK_BASES


def add_arguments(parser):
    parser.add_argument("--device", action="append", help="Use only this paired Bluetooth device; repeat for multiple devices")
    parser.add_argument("--base-note", type=int, help=f"Use a fixed bottom-left note instead of automatic banks {BANK_BASES}")
    parser.add_argument("--brightness", type=float, default=0.25, help="Maximum brightness, 0–1 (default: 0.25)")
    parser.add_argument("--fps", type=float, default=8, help="Target updates/second (default: 8)")
    parser.add_argument("--chunk-delay", type=float, default=0.03, help="BLE chunk gap in seconds (default: 0.03)")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds; otherwise run until Ctrl+C")


def validate_arguments(parser, args):
    if args.device and any(not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", a) for a in args.device):
        parser.error("device must have the form AA:BB:CC:DD:EE:FF")
    if args.base_note is not None and not 0 <= args.base_note <= 112:
        parser.error("base-note must be between 0 and 112")
    for name, low, high in (
        ("brightness", 0.001, 1), ("fps", 1, 60), ("chunk_delay", 0.005, 1),
    ):
        if not low <= getattr(args, name) <= high:
            parser.error(f"{name.replace('_', '-')} must be between {low} and {high}")
    if args.duration is not None and not (math.isfinite(args.duration) and args.duration > 0):
        parser.error("duration must be a positive finite number")


def run(args, interaction, instructions=None):
    from engine import Engine
    engine = Engine(auto_join=not args.device)
    def stop(signum, frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    options = {key: value for key, value in vars(args).items() if key not in ("device", "duration")}
    try:
        engine.start()
        engine.call('start', interaction=interaction, devices=args.device, options=options)
        print((instructions or "Interaction running") + "; Ctrl+C to stop.", flush=True)
        started = time.monotonic()
        while args.duration is None or time.monotonic() - started < args.duration:
            time.sleep(.1)
    except KeyboardInterrupt:
        print("Stopping interaction.", flush=True)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        engine.close()
    return 0
