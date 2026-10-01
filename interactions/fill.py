#!/usr/bin/env python3
"""Fill every JP MINI pad with one color before moving to the next."""

import argparse
import math
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interactions.coloring import Coloring
from interactions.runtime import InteractionWorker, add_arguments, validate_arguments, run

# Alternate strongly differing RGB colors, including the wrap back to black.
PALETTE = (
    ("black", (0, 0, 0)),
    ("white", (255, 255, 255)),
    ("blue", (0, 0, 255)),
    ("yellow", (255, 255, 0)),
    ("magenta", (255, 0, 255)),
    ("green", (0, 255, 0)),
    ("red", (255, 0, 0)),
    ("cyan", (0, 255, 255)),
)

# Bottom-left, around the rim, then into the center. One step per default frame.
SPIRAL = (0, 1, 2, 3, 7, 11, 15, 14, 13, 12, 8, 4, 5, 6, 10, 9)
CHASE_SECONDS = 2.0
PULSE_SECONDS = 0.625
CELEBRATION_SECONDS = CHASE_SECONDS + 2 * PULSE_SECONDS


class Fill(Coloring):
    """Only pixels left over from the previous round can advance."""

    palette = PALETTE

    def __init__(self, base_note=None, brightness=0.25):
        super().__init__(base_note, brightness)
        self.target = 1
        self.completed_at = None
        self.event_time = 0

    def handle(self, event):
        self.event_time = event.time
        return super().handle(event)

    def reconnect(self):
        super().reconnect()
        self.completed_at = None  # Resume the completed board, without replaying.

    def advance(self, pad):
        if self.completed_at is not None and self.event_time - self.completed_at < CELEBRATION_SECONDS:
            return False  # Still track presses/releases, but do not queue moves.
        if self.pixels[pad] == self.target:
            return False
        self.pixels[pad] = self.target
        if all(pixel == self.target for pixel in self.pixels):
            self.completed_at = self.event_time
            self.target = (self.target + 1) % len(self.palette)
        return True

    def render(self, now):
        elapsed = None if self.completed_at is None else now - self.completed_at
        if elapsed is None or not 0 <= elapsed < CELEBRATION_SECONDS:
            return super().render(now)
        color = self.palette[self.pixels[0]][1]
        accent = self.palette[(self.pixels[0] - 1) % len(self.palette)][1]
        if PULSE_SECONDS <= elapsed < PULSE_SECONDS + CHASE_SECONDS:
            chase_time = elapsed - PULSE_SECONDS
            cursor = chase_time / CHASE_SECONDS * len(SPIRAL)
            # Return to the completed color at each phase boundary.
            edge = min(1, chase_time / 0.125, (CHASE_SECONDS - chase_time) / 0.125)
            envelope = edge * edge * (3 - 2 * edge)
            amounts = [0] * 16
            for step, pad in enumerate(SPIRAL):
                # A broad head and soft trailing glow stay legible at 8 FPS.
                distance = cursor - step
                glow = math.exp(-0.5 * (distance / (1.4 if distance >= 0 else 0.5)) ** 2)
                amounts[pad] = glow * envelope
        else:
            pulse_time = elapsed if elapsed < PULSE_SECONDS else elapsed - PULSE_SECONDS - CHASE_SECONDS
            amounts = [math.sin(math.pi * pulse_time / PULSE_SECONDS) ** 2] * 16
        return [tuple(round((base * (1 - amount) + highlight * amount) * self.brightness)
                      for base, highlight in zip(color, accent)) for amount in amounts]


class FillWorker(InteractionWorker):
    def __init__(self, address, args, session_factory):
        super().__init__(address, args, session_factory,
                         lambda: Fill(args.base_note, args.brightness), "fill")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    validate_arguments(parser, args)
    return run(args, FillWorker, "Tap every pad to fill the next color")


if __name__ == "__main__":
    sys.exit(main())
