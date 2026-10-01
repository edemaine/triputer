"""Coloring behavior without Bluetooth hardware."""

import unittest

from controllers.midi import MidiEvent
from interactions.coloring import Coloring, PALETTE


class ColoringTests(unittest.TestCase):
    def event(self, kind="on", note=4, value=100):
        return MidiEvent(kind, 9, note, value, 0)

    def test_starts_black_and_cycles_one_pixel_back_to_black(self):
        canvas = Coloring(brightness=1)
        self.assertEqual(canvas.render(0), [(0, 0, 0)] * 16)
        for index in range(1, len(PALETTE) + 1):
            canvas.handle(self.event())
            frame = canvas.render(0)
            self.assertEqual(frame[0], PALETTE[index % len(PALETTE)][1])
            self.assertEqual(frame[1:], [(0, 0, 0)] * 15)
            canvas.handle(self.event("off"))

    def test_holding_pressure_and_duplicate_notes_do_not_cycle(self):
        canvas = Coloring()
        canvas.handle(self.event())
        expected = canvas.render(0)
        for _ in range(100):
            canvas.handle(self.event())
            canvas.handle(self.event("pressure"))
        self.assertEqual(canvas.render(100), expected)
        canvas.handle(self.event(value=0))
        canvas.handle(self.event())
        self.assertEqual(canvas.pixels[0], 2)

    def test_banks_share_canvas_and_other_controllers_do_not(self):
        first, second = Coloring(), Coloring()
        for note in (4, 36, 52, 68):
            first.handle(self.event(note=note))
        self.assertEqual(first.pixels[0], 4)
        self.assertEqual(second.pixels, [0] * 16)
        self.assertEqual(first.handle(self.event(note=83)), ("red", (4, 4)))
        self.assertEqual(first.pixels[15], 1)

    def test_reconnect_retains_art_but_clears_held_keys(self):
        canvas = Coloring()
        canvas.handle(self.event())
        frame = canvas.render(0)
        canvas.reconnect()
        self.assertEqual(canvas.render(100), frame)
        canvas.handle(self.event())
        self.assertEqual(canvas.pixels[0], 2)

    def test_custom_range_and_unmapped_notes(self):
        canvas = Coloring(base_note=60)
        self.assertIsNone(canvas.handle(self.event(note=4)))
        self.assertIsNone(canvas.handle(self.event(note=76)))
        self.assertEqual(canvas.handle(self.event(note=60)), ("red", (1, 1)))
        self.assertEqual(canvas.handle(self.event(note=75)), ("red", (4, 4)))
        self.assertTrue(all(0 <= c <= 64 for rgb in canvas.render(0) for c in rgb))


if __name__ == "__main__":
    unittest.main()
