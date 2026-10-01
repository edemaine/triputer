#!/usr/bin/env python3
"""Hold JP MINI pads to emit colored ripples; release to let them fade."""

import argparse
import colorsys
import math
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # Direct execution needs the project root to find sibling packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controllers.bluetooth import (
    BluetoothSession, BusPool, managed_objects, paired_controllers,
    start_dbus_loop,
)
from controllers.jpmini import note_to_position

PALETTES = {
    base: [colorsys.hsv_to_rgb((i / 16 + shift) % 1, 1, 1) for i in range(16)]
    for base, shift in ((4, 0), (36, 1 / 3), (52, 2 / 3), (68, 1 / 6))
}
DISTANCES = [
    [math.hypot(a // 4 - b // 4, a % 4 - b % 4) for b in range(16)]
    for a in range(16)
]
# Consecutive banks starting at 36, plus the observed 4–19 preset.
BANK_BASES = tuple(PALETTES)


@dataclass
class Source:
    pad: int
    started: float
    color: tuple
    released: float | None = None


class Ripples:
    """Time-based animation; independent of Bluetooth and render frame rate."""

    def __init__(self, base_note=None, brightness=0.25, speed=2.5, period=1.2, fade=2,
                 initial_bank=None):
        self.base_note = base_note
        self.brightness = brightness
        self.speed = speed
        self.period = period
        self.fade = fade
        self.sources = []
        self.held = {}
        self.active_bank = initial_bank
        self.palette = (PALETTES[4] if base_note is not None else
                        PALETTES[initial_bank] if initial_bank is not None else None)

    def bank_for(self, note):
        bases = BANK_BASES if self.base_note is None else (self.base_note,)
        return next((base for base in bases if 0 <= note - base < 16), None)

    def handle(self, event):
        bank = self.bank_for(event.note)
        if bank is None or event.kind == "pressure":
            return None
        position = note_to_position(event.note, bank)
        key = (event.channel, event.note)
        if event.kind == "on" and event.value > 0:
            if self.active_bank != bank:
                # Bank switching may suppress old note-offs. Let those sources
                # fade instead of leaving them held indefinitely.
                for source in self.held.values():
                    source.released = event.time
                self.held.clear()
                self.active_bank = bank
                self.palette = PALETTES[bank] if self.base_note is None else PALETTES[4]
            if key in self.held:
                return None  # Repeated note-ons do not restart a held source.
            row, column = position
            pad = (row - 1) * 4 + column - 1
            # Re-pressing a fading pad resumes its original pulse clock. Stacking
            # a fresh wave train per tap fills every gap between moving rings.
            source = next((source for source in reversed(self.sources)
                           if source.pad == pad and source.color == self.palette[pad]
                           and source.released is not None
                           and 0 <= event.time - source.released < self.fade), None)
            if source is None:
                source = Source(pad, event.time, self.palette[pad])
                self.sources.append(source)
            else:
                source.released = None
            self.held[key] = source
            return "press", position
        if event.kind == "off" or (event.kind == "on" and event.value == 0):
            source = self.held.pop(key, None)
            if source is not None:
                source.released = event.time
                return "release", position
        return None

    def render(self, now):
        self.sources = [
            source for source in self.sources
            if source.released is None or now - source.released < self.fade
        ]
        # A dim base color identifies what each pad will emit when pressed.
        colors = [[0.04 * channel for channel in color] for color in
                  (self.palette or [(0, 0, 0)] * 16)]
        width = 0.55  # Broad rings remain legible on a 4x4 grid.
        lifetime = (math.sqrt(18) + 3 * width) / self.speed
        for source in self.sources:
            if now < source.started:
                continue
            envelope = 1.0
            if source.released is not None:
                remaining = max(0, min(1, 1 - (now - source.released) / self.fade))
                envelope = remaining * remaining * (3 - 2 * remaining)
            end = now if source.released is None else min(now, source.released)
            first = max(0, math.ceil((now - lifetime - source.started) / self.period))
            last = math.floor((end - source.started) / self.period)
            ages = [now - (source.started + pulse * self.period) for pulse in range(first, last + 1)]
            for pad, distance in enumerate(DISTANCES[source.pad]):
                amount = sum(
                    0.85 * math.exp(-0.5 * ((distance - self.speed * age) / width) ** 2)
                    / (1 + 0.2 * age)
                    for age in ages
                )
                if source.released is None and pad == source.pad:
                    amount = max(amount, 0.7)
                amount = min(1, amount) * envelope
                # Screen blending mixes overlaps smoothly without clipping RGB sums.
                for channel in range(3):
                    colors[pad][channel] = 1 - (1 - colors[pad][channel]) * (
                        1 - amount * source.color[channel]
                    )
        return [tuple(round(c * self.brightness * 255) for c in color) for color in colors]


class RippleWorker(threading.Thread):
    """One reconnect loop and animation per controller; no shared pad state."""

    def __init__(self, address, args, session_factory):
        super().__init__(name=f"ripple-{address}", daemon=True)
        self.address = address
        self.args = args
        self.session_factory = session_factory
        self.stop_event = threading.Event()
        self.frames = self.events = 0

    def run(self):
        failures = 0
        last_bank = None
        while not self.stop_event.is_set():
            session = None
            try:
                session = self.session_factory(self.address, self.args.chunk_delay)
                session.open(self.stop_event)
                # Discard held keys, tails, and queued events from the old link.
                animation = Ripples(
                    self.args.base_note, self.args.brightness, self.args.speed,
                    self.args.period, self.args.fade,
                    initial_bank=last_bank,
                )
                connected_at = time.monotonic()
                unmapped = set()
                print(f"[{self.address}] Connected; ripples ready.", flush=True)
                while not self.stop_event.is_set():
                    frame_start = time.monotonic()
                    for event in session.drain():
                        if animation.bank_for(event.note) is None and event.note not in unmapped:
                            unmapped.add(event.note)
                            print(f"[{self.address}] Unmapped note {event.note}; use --base-note for a custom preset.", flush=True)
                        change = animation.handle(event)
                        last_bank = animation.active_bank
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", action="append", help="Use only this paired Bluetooth address; repeat for multiple devices")
    parser.add_argument("--base-note", type=int, help=f"Use a fixed bottom-left note instead of automatic banks {BANK_BASES}")
    parser.add_argument("--brightness", type=float, default=0.25, help="Maximum brightness, 0–1 (default: 0.25)")
    parser.add_argument("--fps", type=float, default=8, help="Target updates/second (default: 8)")
    parser.add_argument("--chunk-delay", type=float, default=0.03, help="BLE chunk gap in seconds (default: 0.03)")
    parser.add_argument("--speed", type=float, default=2.5, help="Ring speed in pads/second (default: 2.5)")
    parser.add_argument("--period", type=float, default=1.2, help="Seconds between rings while held (default: 1.2)")
    parser.add_argument("--fade", type=float, default=2, help="Seconds to fade after release (default: 2)")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds; otherwise run until Ctrl+C")
    args = parser.parse_args()
    if args.address and any(not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", a) for a in args.address):
        parser.error("address must have the form AA:BB:CC:DD:EE:FF")
    if args.base_note is not None and not 0 <= args.base_note <= 112:
        parser.error("base-note must be between 0 and 112")
    for name, low, high in (
        ("brightness", 0.001, 1), ("fps", 1, 60), ("chunk_delay", 0.005, 1),
        ("speed", 0.1, 20), ("period", 0.1, 30), ("fade", 0.1, 60),
    ):
        if not low <= getattr(args, name) <= high:
            parser.error(f"{name.replace('_', '-')} must be between {low} and {high}")
    if args.duration is not None and not (math.isfinite(args.duration) and args.duration > 0):
        parser.error("duration must be a positive finite number")

    def stop(signum, frame):
        raise KeyboardInterrupt

    # Also clear lights when stopped via SSH or a process supervisor.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    buses = BusPool()
    group = ControllerGroup(lambda address: RippleWorker(
        address, args,
        lambda address, delay: BluetoothSession(address, delay, buses.get(address)),
    ))
    loop = thread = bus = None
    status = 0
    started = time.monotonic()
    try:
        loop, thread = start_dbus_loop()
        from gi.repository import GLib
        print("Waiting for paired JP MINIs. Hold pads for ripples; Ctrl+C to stop.", flush=True)
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
        print("\nStopping ripples.", flush=True)
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


if __name__ == "__main__":
    sys.exit(main())
