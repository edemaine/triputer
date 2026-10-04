#!/usr/bin/env python3
"""Exercise JP MINI Bluetooth pad colors while monitoring ALSA MIDI input."""

import argparse
import math
import sys
import time
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # Direct execution needs the project root to find sibling packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controllers.bluetooth import BLACK, PadLights
from controllers.midi import MidiMonitor
from engine.lock import EngineLock


def run_test(lights, brightness, fps, hold):
    level = round(255 * brightness)
    primary = [(level, 0, 0), (0, level, 0), (0, 0, level)]
    print("Pad walk: bottom-left to bottom-right, then upward; red/green/blue repeating.", flush=True)
    for pad in range(16):
        colors = BLACK.copy()
        colors[pad] = primary[pad % 3]
        print(f"Pad {pad + 1}: row {pad // 4 + 1} from bottom, column {pad % 4 + 1}", flush=True)
        lights.send(colors)
        time.sleep(0.35)

    for name, color in zip(("red", "green", "blue"), primary):
        print(f"All pads {name}", flush=True)
        lights.send([color] * 16)
        time.sleep(1)

    print("Two gentle RGB fades (8 seconds). Try pressing pads now.", flush=True)
    start = time.monotonic()
    frames = 0
    while (elapsed := time.monotonic() - start) < 8:
        frame_start = time.monotonic()
        intensity = (1 - math.cos(2 * math.pi * elapsed / 4)) / 2
        colors = [tuple(round(c * intensity) for c in primary[i % 3]) for i in range(16)]
        lights.send(colors)
        frames += 1
        time.sleep(max(0, 1 / fps - (time.monotonic() - frame_start)))
    print(f"Sent {frames / (time.monotonic() - start):.1f} full grids/second during fades.", flush=True)

    print(f"Holding RGB pattern for {hold:g} seconds; check MIDI input and local LED overrides.", flush=True)
    lights.send([primary[i % 3] for i in range(16)])
    time.sleep(hold)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Bluetooth address; defaults to the sole connected JP-Mini")
    parser.add_argument("--midi-port", help="ALSA input port; defaults to the sole JP-Mini port")
    parser.add_argument("--base-note", type=int, default=4, help="Bottom-left pad's note, 0–112 (default: 4)")
    parser.add_argument("--brightness", type=float, default=0.25, help="Maximum brightness, 0–1 (default: 0.25)")
    parser.add_argument("--fps", type=float, default=8, help="Target fade updates/second (default: 8)")
    parser.add_argument("--chunk-delay", type=float, default=0.03, help="Seconds between BLE chunks (default: 0.03)")
    parser.add_argument("--hold", type=float, default=10, help="Seconds to hold final RGB pattern (default: 10)")
    args = parser.parse_args()
    if not 0 <= args.base_note <= 112:
        parser.error("Require base-note in [0,112], leaving room for 16 MIDI notes.")
    if not (0 < args.brightness <= 1 and 0 < args.fps <= 60
            and 0.005 <= args.chunk_delay <= 1 and 0 <= args.hold <= 3600):
        parser.error("Require brightness in (0,1], fps in (0,60], chunk-delay in [0.005,1], hold in [0,3600].")
    lights = monitor = None
    hardware_lock = EngineLock()
    status = 0
    try:
        hardware_lock.acquire()
        monitor = MidiMonitor(args.midi_port, args.base_note)
        lights = PadLights(args.device, args.chunk_delay)
        run_test(lights, args.brightness, args.fps, args.hold)
        if monitor.process.poll() is not None:
            raise RuntimeError("MIDI monitor exited unexpectedly; inspect the MIDI output above.")
    except KeyboardInterrupt:
        print("\nStopping test.", flush=True)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr, flush=True)
        status = 1
    finally:
        if lights is not None:
            try:
                lights.close()
                print(f"Sent {lights.frames} grids; pads cleared. Bluetooth remains connected.", flush=True)
            except Exception as error:
                print(f"Could not clear pads: {error}", file=sys.stderr)
                status = 1
        if monitor is not None:
            monitor.close()
            print(f"Observed {monitor.events} MIDI events.", flush=True)
            if not monitor.events:
                print("No pad input observed; press pads during another run to verify simultaneous input.", flush=True)
        hardware_lock.close()
    return status


if __name__ == "__main__":
    sys.exit(main())
