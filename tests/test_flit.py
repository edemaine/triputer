"""Free toggles, direction changes, and responsive celebrations for Flit."""

import unittest

from controllers.midi import MidiEvent
from interactions.fill import PALETTE, SPIRAL, CHASE_SECONDS, PULSE_SECONDS, CELEBRATION_SECONDS
from interactions.flit import Flit


class FlitTests(unittest.TestCase):
    def setUp(self):
        self.now = 0

    def event(self, pad=0, kind="on", value=100, base=4):
        return MidiEvent(kind, 9, base + pad, value, self.now)

    def tap(self, game, pad=0, base=4):
        result = game.handle(self.event(pad, base=base))
        game.handle(self.event(pad, "off", base=base))
        return result

    def fill(self, game):
        for pad in range(16):
            self.tap(game, pad)

    def test_taps_freely_toggle_both_colors_in_a_mixed_grid(self):
        game = Flit(brightness=1)
        self.tap(game, 1)
        for _ in range(10):
            self.assertEqual(self.tap(game), ("white", (1, 1)))
            self.assertEqual(self.tap(game), ("black", (1, 1)))
        self.assertEqual(game.pixels, [0, 1] + [0] * 14)
        self.assertIsNone(game.completed_at)
        self.assertEqual((game.current, game.target, game.direction), (0, 1, 1))

    def test_newer_color_continues_and_older_color_reverses(self):
        game = Flit()
        self.fill(game)
        self.assertEqual((game.current, game.target, game.direction), (1, 2, 1))
        self.now += CELEBRATION_SECONDS
        self.tap(game, 0)
        self.tap(game, 1)
        self.tap(game, 0)
        self.assertIsNone(game.completed_at)
        self.tap(game, 1)
        self.assertEqual(game.pixels, [1] * 16)
        self.assertEqual((game.current, game.target, game.direction), (1, 0, -1))
        self.assertEqual(game.completed_at, self.now)
        self.now += CELEBRATION_SECONDS
        self.fill(game)
        self.assertEqual((game.current, game.target, game.direction), (0, 7, -1))
        self.now += CELEBRATION_SECONDS
        self.tap(game)
        self.tap(game)
        self.assertEqual((game.current, game.target, game.direction), (0, 1, 1))

    def test_palette_wraps_in_both_directions(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                game = Flit()
                if direction == -1:
                    self.tap(game)
                    self.tap(game)  # Back to black: the next color is cyan.
                    self.now += CELEBRATION_SECONDS
                for round_number in range(1, 2 * len(PALETTE) + 1):
                    self.fill(game)
                    expected = round_number * direction % len(PALETTE)
                    self.assertEqual(game.pixels, [expected] * 16)
                    self.assertEqual(game.current, expected)
                    self.assertEqual(game.target, (expected + direction) % len(PALETTE))
                    self.assertEqual(game.direction, direction)
                    self.now += CELEBRATION_SECONDS

    def test_celebration_uses_other_color_in_both_directions(self):
        game = Flit(brightness=.25)
        self.fill(game)
        for completed, accent in ((1, 0), (1, 2), (0, 1), (7, 0)):
            with self.subTest(completed=completed, accent=accent):
                base = tuple(round(c * game.brightness) for c in PALETTE[completed][1])
                highlight = tuple(round(c * game.brightness) for c in PALETTE[accent][1])
                for offset in (0, PULSE_SECONDS, PULSE_SECONDS + CHASE_SECONDS, CELEBRATION_SECONDS):
                    self.assertEqual(game.render(self.now + offset), [base] * 16)
                for offset in (PULSE_SECONDS / 2, CHASE_SECONDS + 1.5 * PULSE_SECONDS):
                    self.assertEqual(game.render(self.now + offset), [highlight] * 16)
                frame = game.render(self.now + PULSE_SECONDS + CHASE_SECONDS / 2)
                self.assertEqual(frame[SPIRAL[8]], highlight)
                self.assertNotEqual(frame[SPIRAL[0]], highlight)
                self.assertEqual(game.pixels, [completed] * 16)
            self.now += CELEBRATION_SECONDS
            if accent == 0 and completed == 1:
                self.tap(game)
                self.tap(game)  # Blue loses: reverse to white/black.
            else:
                self.fill(game)

    def test_tapping_during_celebration_toggles_and_cancels_effect(self):
        game = Flit(brightness=1)
        self.fill(game)
        self.now = PULSE_SECONDS / 2
        self.assertEqual(game.render(self.now), [(0, 0, 0)] * 16)
        self.assertEqual(self.tap(game), ("blue", (1, 1)))
        self.assertIsNone(game.completed_at)
        self.assertEqual(game.render(self.now), [(0, 0, 255)] + [(255, 255, 255)] * 15)
        self.assertEqual(self.tap(game), ("white", (1, 1)))
        self.assertEqual(game.completed_at, self.now)
        self.assertEqual(game.target, 0)
        self.now += .1
        self.assertEqual(self.tap(game), ("black", (1, 1)))
        self.assertIsNone(game.completed_at)

    def test_holds_pressure_and_zero_velocity_releases(self):
        game = Flit()
        for pad in range(15):
            self.tap(game, pad)
        self.assertEqual(game.handle(self.event(15)), ("white", (4, 4)))
        completed_at = game.completed_at
        for offset in (.1, CELEBRATION_SECONDS):
            self.now = offset
            self.assertIsNone(game.handle(self.event(15)))
            self.assertIsNone(game.handle(self.event(15, "pressure")))
            self.assertEqual(game.completed_at, completed_at)
            self.assertEqual(game.pixels, [1] * 16)
        game.handle(self.event(15, value=0))
        self.assertEqual(game.handle(self.event(15)), ("blue", (4, 4)))

    def test_bank_changes_and_reconnect_preserve_pair_and_direction(self):
        game = Flit()
        self.tap(game)
        self.tap(game, base=68)  # Return to black and reverse.
        game.handle(self.event(base=52))
        self.assertEqual(game.pixels, [7] + [0] * 15)
        game.reconnect()
        self.assertEqual(game.pixels, [7] + [0] * 15)
        self.assertEqual((game.current, game.target, game.direction), (0, 7, -1))
        self.assertEqual(self.tap(game, base=52), ("black", (1, 1)))
        game.reconnect()  # Skip the effect without changing the next pair.
        self.assertIsNone(game.completed_at)
        self.assertEqual((game.current, game.target, game.direction), (0, 1, 1))
        self.assertEqual(self.tap(game, base=36), ("white", (1, 1)))
        self.assertEqual(Flit().pixels, [0] * 16)

    def test_custom_note_range_and_brightness(self):
        game = Flit(base_note=60, brightness=.1)
        self.assertIsNone(self.tap(game))
        self.assertEqual(self.tap(game, 15, base=60), ("white", (4, 4)))
        self.assertEqual(game.render(0)[15], (26, 26, 26))
        self.assertEqual(self.tap(game, 15, base=60), ("black", (4, 4)))
        self.assertEqual(game.target, 7)


if __name__ == "__main__":
    unittest.main()
