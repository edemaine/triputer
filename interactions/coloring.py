#!/usr/bin/env python3
"""Tap JP MINI pads to cycle colors and draw 4x4 pixel art."""

import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controllers.jpmini import note_bank, note_to_position
from interactions.runtime import InteractionWorker, add_arguments, validate_arguments, run

PALETTE = (
    ("black", (0, 0, 0)),
    ("red", (255, 0, 0)),
    ("orange", (255, 96, 0)),
    ("yellow", (255, 255, 0)),
    ("green", (0, 255, 0)),
    ("cyan", (0, 255, 255)),
    ("blue", (0, 0, 255)),
    ("purple", (128, 0, 255)),
    ("pink", (255, 0, 128)),
    ("white", (255, 255, 255)),
)


class Coloring:
    """One canvas per controller; taps change pixels, pressure and holds do not."""

    def __init__(self, base_note=None, brightness=0.25):
        self.base_note = base_note
        self.brightness = brightness
        self.pixels = [0] * 16
        self.held = set()
        self.active_bank = None

    def reconnect(self):
        # A release may have been lost, but the drawing survives reconnection.
        self.held.clear()

    def bank_for(self, note):
        return note_bank(note, self.base_note)

    def handle(self, event):
        bank = self.bank_for(event.note)
        if bank is None or event.kind == "pressure":
            return None
        key = (event.channel, event.note)
        if event.kind == "off" or (event.kind == "on" and event.value == 0):
            self.held.discard(key)
            return None
        if event.kind != "on":
            return None
        if bank != self.active_bank:
            self.held.clear()
            self.active_bank = bank
        if key in self.held:
            return None
        self.held.add(key)
        position = note_to_position(event.note, bank)
        row, column = position
        pad = (row - 1) * 4 + column - 1
        self.pixels[pad] = (self.pixels[pad] + 1) % len(PALETTE)
        return PALETTE[self.pixels[pad]][0], position

    def render(self, now):
        return [tuple(round(channel * self.brightness) for channel in PALETTE[index][1])
                for index in self.pixels]


class ColoringWorker(InteractionWorker):
    def __init__(self, address, args, session_factory):
        super().__init__(address, args, session_factory,
                         lambda: Coloring(args.base_note, args.brightness), "coloring")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args()
    validate_arguments(parser, args)
    return run(args, ColoringWorker, "Tap pads to cycle colors")


if __name__ == "__main__":
    sys.exit(main())
