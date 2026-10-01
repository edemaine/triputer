"""Shared controller discovery, reconnect loops, and CLI options for interactions."""

import math
import re
import signal
import sys
import threading
import time

from controllers.bluetooth import (
    BluetoothSession, BusPool, managed_objects, paired_controllers, start_dbus_loop,
)
from controllers.jpmini import BANK_BASES


class InteractionWorker(threading.Thread):
    """One reconnect loop and animation per controller; no shared pad state."""

    def __init__(self, address, args, session_factory, animation_factory, label):
        super().__init__(name=f"{label}-{address}", daemon=True)
        self.animation_factory = animation_factory
        self.label = label
        self.address = address
        self.args = args
        self.session_factory = session_factory
        self.stop_event = threading.Event()
        self.frames = self.events = 0

    def run(self):
        failures = 0
        animation = self.animation_factory()
        while not self.stop_event.is_set():
            session = None
            try:
                session = self.session_factory(self.address, self.args.chunk_delay)
                session.open(self.stop_event)
                animation.reconnect()
                connected_at = time.monotonic()
                unmapped = set()
                print(f"[{self.address}] Connected; {self.label} ready.", flush=True)
                while not self.stop_event.is_set():
                    frame_start = time.monotonic()
                    for event in session.drain():
                        if animation.bank_for(event.note) is None and event.note not in unmapped:
                            unmapped.add(event.note)
                            print(f"[{self.address}] Unmapped note {event.note}; use --base-note for a custom preset.", flush=True)
                        change = animation.handle(event)
                        if change:
                            action, (row, column) = change
                            print(f"[{self.address}] {action}: row {row}, column {column}", flush=True)
                    session.send(animation.render(time.monotonic()))
                    self.frames += 1
                    if time.monotonic() - connected_at >= 5:
                        failures = 0
                    self.stop_event.wait(max(0, 1 / self.args.fps - (time.monotonic() - frame_start)))
            except Exception as error:
                if not self.stop_event.is_set():
                    failures += 1
                    delay = min(15, 2 ** min(failures, 4))
                    print(f"[{self.address}] {error}; retrying in {delay}s.", flush=True)
            finally:
                if session is not None:
                    self.events += session.events
                    try:
                        session.close(clear=self.stop_event.is_set())
                    except Exception as error:
                        print(f"[{self.address}] Session cleanup: {error}", flush=True)
            if not self.stop_event.is_set():
                self.stop_event.wait(delay)
        print(f"[{self.address}] Stopped; {self.events} MIDI events, {self.frames} grids sent.", flush=True)


class ControllerGroup:
    def __init__(self, factory):
        self.factory = factory
        self.workers = {}

    def update(self, addresses):
        for address, worker in list(self.workers.items()):
            if address not in addresses:
                worker.stop_event.set()
            if not worker.is_alive():
                del self.workers[address]
        for address in sorted(addresses):
            if address not in self.workers:
                worker = self.factory(address)
                self.workers[address] = worker
                worker.start()

    def close(self):
        for worker in self.workers.values():
            worker.stop_event.set()
        for worker in self.workers.values():
            # Calls have explicit timeouts. Finish cleanup before closing buses;
            # an arbitrary join deadline could leave a worker using a closed bus.
            worker.join()


def add_arguments(parser):
    parser.add_argument("--address", action="append", help="Use only this paired Bluetooth address; repeat for multiple devices")
    parser.add_argument("--base-note", type=int, help=f"Use a fixed bottom-left note instead of automatic banks {BANK_BASES}")
    parser.add_argument("--brightness", type=float, default=0.25, help="Maximum brightness, 0–1 (default: 0.25)")
    parser.add_argument("--fps", type=float, default=8, help="Target updates/second (default: 8)")
    parser.add_argument("--chunk-delay", type=float, default=0.03, help="BLE chunk gap in seconds (default: 0.03)")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds; otherwise run until Ctrl+C")


def validate_arguments(parser, args):
    if args.address and any(not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", a) for a in args.address):
        parser.error("address must have the form AA:BB:CC:DD:EE:FF")
    if args.base_note is not None and not 0 <= args.base_note <= 112:
        parser.error("base-note must be between 0 and 112")
    for name, low, high in (
        ("brightness", 0.001, 1), ("fps", 1, 60), ("chunk_delay", 0.005, 1),
    ):
        if not low <= getattr(args, name) <= high:
            parser.error(f"{name.replace('_', '-')} must be between {low} and {high}")
    if args.duration is not None and not (math.isfinite(args.duration) and args.duration > 0):
        parser.error("duration must be a positive finite number")


def run(args, worker_factory, instructions):
    def stop(signum, frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    buses = BusPool()
    group = ControllerGroup(lambda address: worker_factory(
        address, args,
        lambda address, delay: BluetoothSession(address, delay, buses.get(address)),
    ))
    loop = thread = None
    status = 0
    started = time.monotonic()
    try:
        loop, thread = start_dbus_loop()
        from gi.repository import GLib
        print(f"Waiting for paired JP MINIs. {instructions}; Ctrl+C to stop.", flush=True)
        last_error = None
        while args.duration is None or time.monotonic() - started < args.duration:
            try:
                bus = buses.get("discovery")
                addresses = {a.upper() for a in args.address} if args.address else paired_controllers(managed_objects(bus))
                group.update(addresses)
                last_error = None
            except GLib.Error as error:
                if str(error) != last_error:
                    print(f"Bluetooth discovery unavailable; will retry: {error}", flush=True)
                    last_error = str(error)
            remaining = 2 if args.duration is None else min(2, max(0, args.duration - (time.monotonic() - started)))
            time.sleep(remaining)
    except KeyboardInterrupt:
        print("\nStopping interaction.", flush=True)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        status = 1
    finally:
        group.close()
        if loop is not None:
            loop.quit()
            thread.join(timeout=2)
        buses.close()
        print("Stopped. Available pads cleared; Bluetooth pairings preserved.", flush=True)
    return status
