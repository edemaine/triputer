#!/usr/bin/env python3
"""Hold JP MINI pads to emit colored ripples; release to let them fade."""

import argparse
import colorsys
import math
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # Direct execution needs the project root to find sibling packages.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controllers.jpmini import note_to_position, note_bank
from interactions.runtime import add_arguments, validate_arguments, run

PALETTES = {
    base: [colorsys.hsv_to_rgb((i / 16 + shift) % 1, 1, 1) for i in range(16)]
    for base, shift in ((4, 0), (36, 1 / 3), (52, 2 / 3), (68, 1 / 6))
}
DISTANCES = [
    [math.hypot(a // 4 - b // 4, a % 4 - b % 4) for b in range(16)]
    for a in range(16)
]


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

    def reconnect(self):
        """Forget missed releases and old ripples, keeping the selected palette."""
        self.sources.clear()
        self.held.clear()

    def bank_for(self, note):
        return note_bank(note, self.base_note)

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument("--speed", type=float, default=2.5, help="Ring speed in pads/second (default: 2.5)")
    parser.add_argument("--period", type=float, default=1.2, help="Seconds between rings while held (default: 1.2)")
    parser.add_argument("--fade", type=float, default=2, help="Seconds to fade after release (default: 2)")
    args = parser.parse_args()
    validate_arguments(parser, args)
    for name, low, high in (("speed", 0.1, 20), ("period", 0.1, 30), ("fade", 0.1, 60)):
        if not low <= getattr(args, name) <= high:
            parser.error(f"{name} must be between {low} and {high}")
    return run(args, 'ripple', "Hold pads for ripples")


if __name__ == "__main__":
    sys.exit(main())
