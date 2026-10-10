#!/usr/bin/env python3
"""Toggle JP MINI pads between neighboring colors; a uniform grid picks the direction."""

import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interactions.fill import Fill
from interactions.runtime import add_arguments, validate_arguments, run


class Flit(Fill):
    """Filling the target continues; returning to the current color reverses."""

    def __init__(self, base_note=None, brightness=0.25):
        super().__init__(base_note, brightness)
        self.current = 0
        self.direction = 1

    def advance(self, pad):
        # A fresh tap always toggles, even if it interrupts a celebration.
        self.completed_at = None
        self.pixels[pad] = self.target if self.pixels[pad] == self.current else self.current
        completed = self.pixels[pad]
        if all(pixel == completed for pixel in self.pixels):
            self.celebration_accent = self.current if completed == self.target else self.target
            if completed == self.current:
                self.direction *= -1
            self.current = completed
            self.target = (completed + self.direction) % len(self.palette)
            self.completed_at = self.event_time
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    validate_arguments(parser, args)
    return run(args, 'flit', "Tap pads to toggle colors; match the grid to continue or reverse")


if __name__ == "__main__":
    sys.exit(main())
