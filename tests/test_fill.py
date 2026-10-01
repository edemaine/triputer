"""Round gating, tap edges, and persistence for Fill."""

import unittest

from controllers.midi import MidiEvent
from interactions.fill import Fill, PALETTE, SPIRAL, CHASE_SECONDS, PULSE_SECONDS, CELEBRATION_SECONDS


class FillTests(unittest.TestCase):
    def setUp(self):
        self.now = 0

    def event(self, pad=0, kind="on", value=100, base=4):
        return MidiEvent(kind, 9, base + pad, value, self.now)

    def tap(self, game, pad=0, base=4):
        result = game.handle(self.event(pad, base=base))
        game.handle(self.event(pad, "off", base=base))
        return result

    def test_cannot_advance_until_every_pad_matches(self):
        game = Fill(brightness=1)
        self.assertEqual(game.render(0), [(0, 0, 0)] * 16)
        self.assertEqual(self.tap(game), ("white", (1, 1)))
        for _ in range(30):
            self.assertIsNone(self.tap(game))
        for pad in range(1, 15):
            self.tap(game, pad)
        self.assertIsNone(self.tap(game))
        self.assertEqual(game.pixels, [1] * 15 + [0])
        self.tap(game, 15)
        self.assertEqual(game.render(0), [(255, 255, 255)] * 16)
        self.now += CELEBRATION_SECONDS
        self.assertEqual(self.tap(game), ("blue", (1, 1)))
        self.assertEqual(game.pixels, [2] + [1] * 15)

    def test_full_palette_wraps_through_black_without_skipping(self):
        game = Fill()
        for round_number in range(1, 2 * len(PALETTE) + 1):
            expected = round_number % len(PALETTE)
            for pad in reversed(range(16)):
                self.tap(game, pad)
                self.assertEqual(game.pixels[pad], expected)
            self.assertEqual(game.pixels, [expected] * 16)
            self.now += CELEBRATION_SECONDS

    def test_hold_on_final_pad_and_blocked_pad_does_not_enter_next_round(self):
        game = Fill()
        for pad in range(15):
            self.tap(game, pad)
        game.handle(self.event())  # Blocked tap, still held when round completes.
        game.handle(self.event(15))
        for pad in (0, 15):
            game.handle(self.event(pad))
            game.handle(self.event(pad, "pressure"))
        self.assertEqual(game.pixels, [1] * 16)
        self.now += CELEBRATION_SECONDS
        game.handle(self.event(15))
        self.assertEqual(game.pixels[15], 1)
        game.handle(self.event(15, value=0))
        game.handle(self.event(15))
        self.assertEqual(game.pixels[15], 2)

    def test_reconnect_and_bank_changes_preserve_progress(self):
        game = Fill()
        self.tap(game)
        self.assertIsNone(self.tap(game, base=68))
        game.reconnect()
        self.assertIsNone(self.tap(game, base=52))
        for pad in range(1, 16):
            self.tap(game, pad, base=36)
        self.assertEqual(game.pixels, [1] * 16)
        self.now += CELEBRATION_SECONDS
        self.assertEqual(self.tap(game, base=68), ("blue", (1, 1)))
        self.assertEqual(Fill().pixels, [0] * 16)

    def test_custom_note_range_and_brightness(self):
        game = Fill(base_note=60, brightness=0.1)
        self.assertIsNone(self.tap(game))
        self.assertEqual(self.tap(game, 15, base=60), ("white", (4, 4)))
        self.assertEqual(game.render(0)[15], (26, 26, 26))

    def test_celebration_pulses_spirals_then_pulses_in_previous_color(self):
        game = Fill(brightness=0.25)
        for pad in range(16):
            self.tap(game, pad)
        self.assertEqual(game.render(PULSE_SECONDS / 2), [(0, 0, 0)] * 16)
        for step in (2, 6, 10, 14):
            frame = game.render(PULSE_SECONDS + step * CHASE_SECONDS / 16)
            self.assertEqual(frame[SPIRAL[step]], (0, 0, 0))
            self.assertEqual(min(range(16), key=lambda pad: sum(frame[pad])), SPIRAL[step])
            self.assertTrue(all(0 <= c <= 64 for pixel in frame for c in pixel))
        self.assertEqual(game.render(CHASE_SECONDS + 1.5 * PULSE_SECONDS), [(0, 0, 0)] * 16)
        self.assertEqual(game.pixels, [1] * 16)
        self.assertEqual(game.render(CELEBRATION_SECONDS), [(64, 64, 64)] * 16)

    def test_every_round_uses_previous_color_and_restores_completed_color(self):
        game = Fill(brightness=1)
        for round_number in range(1, len(PALETTE) + 1):
            for pad in range(16):
                self.tap(game, pad)
            base = PALETTE[round_number % len(PALETTE)][1]
            accent = PALETTE[(round_number - 1) % len(PALETTE)][1]
            for offset in (PULSE_SECONDS / 2, PULSE_SECONDS * 1.5 + CHASE_SECONDS):
                self.assertEqual(game.render(self.now + offset), [accent] * 16)
            for offset in (0, PULSE_SECONDS, PULSE_SECONDS + CHASE_SECONDS, CELEBRATION_SECONDS):
                self.assertEqual(game.render(self.now + offset), [base] * 16)
            frame = game.render(self.now + PULSE_SECONDS + CHASE_SECONDS / 2)
            self.assertEqual(frame[SPIRAL[8]], accent)
            self.assertNotEqual(frame[SPIRAL[0]], accent)
            self.now += CELEBRATION_SECONDS

    def test_celebration_taps_are_not_queued_and_reconnect_skips_effect(self):
        game = Fill()
        for pad in range(16):
            self.tap(game, pad)
        self.now = 0.5
        self.assertIsNone(self.tap(game))
        game.handle(self.event(1))
        self.now = CELEBRATION_SECONDS
        game.handle(self.event(1))  # Held through the effect: needs a fresh tap.
        self.assertEqual(game.pixels, [1] * 16)
        self.assertEqual(self.tap(game), ("blue", (1, 1)))
        game.reconnect()
        self.assertIsNone(game.completed_at)
        self.assertEqual(game.pixels, [2] + [1] * 15)

    def test_black_round_also_celebrates_and_returns_to_black(self):
        game = Fill()
        for _ in range(len(PALETTE)):
            for pad in range(16):
                self.tap(game, pad)
            self.now += CELEBRATION_SECONDS
        self.assertEqual(game.pixels, [0] * 16)
        self.assertNotEqual(game.render(game.completed_at + 0.5), [(0, 0, 0)] * 16)
        self.assertEqual(game.render(self.now), [(0, 0, 0)] * 16)


if __name__ == "__main__":
    unittest.main()
