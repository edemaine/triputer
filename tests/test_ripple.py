"""Hardware-independent checks: python3 -m unittest discover -s tests -v."""

import unittest

from controllers.midi import MidiEvent, parse_event
from interactions.ripple import Ripples


class RippleTests(unittest.TestCase):
    def event(self, kind, note, when, value=100):
        return MidiEvent(kind, 9, note, value, when)

    def test_alsa_events_and_zero_velocity_release(self):
        cases = [
            ("128:0   Note on                 9, note 9, velocity 100", "on", 100),
            ("128:0   Note off                9, note 9, velocity 0", "off", 0),
            ("128:0   Note on                 9, note 9, velocity 0", "off", 0),
            ("128:0   Polyphonic aftertouch   9, note 9, value 121", "pressure", 121),
        ]
        for line, kind, value in cases:
            self.assertEqual(parse_event(line, 2), self.event(kind, 9, 2, value))
        self.assertIsNone(parse_event("Waiting for data.", 0))

    def test_ring_moves_outward(self):
        animation = Ripples(period=10)
        animation.handle(self.event("on", 4, 0))
        before = animation.render(0)
        after = animation.render(1 / animation.speed)
        self.assertGreater(before[0][0], before[1][0])
        self.assertGreater(after[1][0], before[1][0])
        self.assertEqual(animation.handle(self.event("on", 19, 0.5)), ("press", (4, 4)))

    def test_banks_preserve_physical_layout_with_rotated_colors(self):
        for offset in range(16):
            frames = []
            for base in (4, 36, 52):
                animation = Ripples()
                self.assertEqual(animation.handle(self.event("on", base + offset, 0)),
                                 ("press", (offset // 4 + 1, offset % 4 + 1)))
                frames.append(animation.render(0.4))
                animation.handle(self.event("off", base + offset, 0.5))
                self.assertFalse(animation.held)
            # Each bank rotates RGB channels, preserving ring geometry/brightness.
            self.assertEqual([tuple((b, r, g)) for r, g, b in frames[0]], frames[1])
            self.assertEqual([tuple((g, b, r)) for r, g, b in frames[0]], frames[2])

    def test_bank_switch_releases_old_sources(self):
        animation = Ripples()
        animation.handle(self.event("on", 4, 0))
        original_color = animation.sources[0].color
        animation.handle(self.event("on", 52, 1))
        self.assertEqual(len(animation.held), 1)
        self.assertEqual(animation.sources[0].released, 1)
        self.assertEqual(animation.sources[0].color, original_color)
        self.assertNotEqual(animation.sources[1].color, original_color)
        animation.render(4)
        self.assertEqual(len(animation.sources), 1)

    def test_third_consecutive_bank_note_82_and_all_pad_positions(self):
        for offset in range(16):
            animation = Ripples()
            self.assertEqual(animation.handle(self.event("on", 68 + offset, 0)),
                             ("press", (offset // 4 + 1, offset % 4 + 1)))
            self.assertNotEqual(animation.render(0.4), [(0, 0, 0)] * 16)
            self.assertEqual(animation.handle(self.event("off", 68 + offset, 1)),
                             ("release", (offset // 4 + 1, offset % 4 + 1)))
            self.assertEqual(animation.render(4), Ripples(initial_bank=68).render(4))
        animation = Ripples()
        animation.handle(self.event("on", 52, 0))
        self.assertEqual(animation.handle(self.event("on", 82, 1)), ("press", (4, 3)))
        self.assertEqual(animation.sources[0].released, 1)
        self.assertEqual(len(animation.held), 1)

    def test_explicit_base_note_overrides_bank_mapping(self):
        animation = Ripples(base_note=60)
        self.assertIsNone(animation.handle(self.event("on", 4, 0)))
        self.assertEqual(animation.handle(self.event("on", 60, 0)), ("press", (1, 1)))
        self.assertEqual(animation.handle(self.event("on", 75, 0)), ("press", (4, 4)))

    def test_release_fades_and_reaches_idle(self):
        animation = Ripples(initial_bank=4)
        idle = animation.render(0)
        animation.handle(self.event("on", 4, 0))
        animation.handle(self.event("off", 4, 0.2))
        self.assertNotEqual(animation.render(0.3), idle)
        self.assertEqual(animation.render(2.2), idle)
        self.assertFalse(animation.sources)

    def test_palette_is_unknown_until_first_press_and_survives_reconnect(self):
        animation = Ripples()
        self.assertEqual(animation.render(0), [(0, 0, 0)] * 16)
        animation.handle(self.event("on", 52, 0))
        animation.handle(self.event("off", 52, 0.1))
        idle = animation.render(3)
        self.assertNotEqual(idle, [(0, 0, 0)] * 16)
        reconnected = Ripples(initial_bank=animation.active_bank)
        self.assertEqual(reconnected.render(4), idle)

    def test_multiple_sources_release_independently(self):
        animation = Ripples()
        animation.handle(self.event("on", 4, 0))
        animation.handle(self.event("on", 19, 0))
        self.assertEqual(len(animation.held), 2)
        animation.handle(self.event("off", 4, 0.2))
        solo = Ripples()
        solo.handle(self.event("on", 19, 0))
        self.assertEqual(animation.render(3), solo.render(3))
        self.assertEqual(len(animation.held), 1)

    def test_pressure_repeated_notes_and_off_grid_are_ignored(self):
        animation = Ripples()
        animation.handle(self.event("on", 9, 0))
        animation.handle(self.event("on", 9, 0.1))
        animation.handle(self.event("pressure", 9, 0.2))
        animation.handle(self.event("on", 20, 0.2))
        self.assertEqual(len(animation.sources), 1)
        self.assertEqual(animation.sources[0].started, 0)
        animation.handle(self.event("on", 9, 0.3, 0))
        self.assertFalse(animation.held)

    def test_rapid_taps_follow_held_rhythm_then_fade(self):
        animation = Ripples()
        held = Ripples()
        held.handle(self.event("on", 4, 0))
        for tap in range(100):
            when = tap * 0.1
            animation.handle(self.event("on", 4, when))
            self.assertEqual(animation.render(when), held.render(when))
            self.assertEqual(len(animation.sources), 1)
            animation.handle(self.event("off", 4, when + 0.04))
            animation.render(when + 0.08)
        self.assertEqual(animation.render(12), Ripples(initial_bank=4).render(12))
        self.assertFalse(animation.sources)

    def test_repress_after_fade_starts_fresh_even_without_render(self):
        animation = Ripples()
        animation.handle(self.event("on", 4, 0))
        animation.handle(self.event("off", 4, 0.1))
        animation.handle(self.event("on", 4, 3))
        fresh = Ripples()
        fresh.handle(self.event("on", 4, 3))
        self.assertEqual(animation.render(3.4), fresh.render(3.4))

    def test_repress_resumes_source_and_color_channels_stay_bounded(self):
        animation = Ripples()
        animation.handle(self.event("on", 4, 0))
        animation.handle(self.event("off", 4, 0.1))
        animation.handle(self.event("on", 4, 0.2))
        self.assertEqual(len(animation.sources), 1)
        self.assertEqual(animation.sources[0].started, 0)
        for note in range(5, 20):
            animation.handle(self.event("on", note, 0.2))
        for when in (0.2, 0.6, 1, 2, 100):
            colors = animation.render(when)
            self.assertEqual(len(colors), 16)
            self.assertTrue(all(0 <= c <= round(255 * 0.25) for rgb in colors for c in rgb))


if __name__ == "__main__":
    unittest.main()
