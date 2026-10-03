"""Preset correction and input gating, without Bluetooth hardware."""
import threading
import unittest
from unittest.mock import Mock, patch

from controllers.bluetooth import BluetoothSession
from controllers.midi import MidiEvent
from controllers.preset import PresetGuard, suite_message
from tests.test_preset_monitor import REPLY


def unpack(message):
    accumulator = bits = 0
    result = bytearray()
    for byte in message[1:-1]:
        accumulator |= byte << bits
        bits += 7
        if bits >= 8:
            result.append(accumulator & 255)
            accumulator >>= 8
            bits -= 8
    return bytes(result)


class PresetGuardTests(unittest.TestCase):
    def guard(self, preset, reject=False):
        state = bytearray(REPLY[6:-1])
        state[3] = preset - 1
        writes = []
        def send(message):
            packet = unpack(message)
            self.assertEqual(packet[-1], ~sum(packet[6:-1]) & 255)
            writes.append(packet)
            if packet[2] == 0x50 and not reject:
                state[:] = packet[6:-1]
            if packet[2] == 0x66:
                reply = b'\x78\x59\x50\x20\0\0' + state + bytes([~sum(state) & 255])
                guard.receive(reply[:30])
                guard.receive(reply[30:])
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        guard = PresetGuard(send, stop, Mock())
        return guard, state, writes

    def test_read_wire_format_matches_hardware(self):
        self.assertEqual(suite_message(0x66).hex(), 'f0783219030000407ff7')

    def test_correct_preset_does_not_write_settings(self):
        guard, state, writes = self.guard(1)
        self.assertFalse(guard.ensure())
        self.assertEqual([p[2] for p in writes], [0x66])

    def test_correction_preserves_every_other_setting_and_verifies(self):
        guard, state, writes = self.guard(8)
        before = bytes(state)
        self.assertTrue(guard.ensure())
        self.assertEqual([p[2] for p in writes], [0x66, 0x50, 0x66])
        self.assertEqual([i for i, (a, b) in enumerate(zip(before, state)) if a != b], [3])
        self.assertEqual(state[3], 0)

    def test_rejected_correction_raises_instead_of_accepting_input(self):
        guard, _, _ = self.guard(2, reject=True)
        with self.assertRaisesRegex(RuntimeError, 'did not verify'):
            guard.ensure()

    def test_missing_reply_fails_closed(self):
        guard = PresetGuard(Mock(), threading.Event())
        with patch('controllers.preset.time.monotonic', side_effect=[0, 4]):
            with self.assertRaisesRegex(RuntimeError, 'input paused'):
                guard.ensure()

    def test_correction_drops_input_and_resets_held_keys(self):
        session = BluetoothSession('AA', .03, Mock())
        session.guard = Mock()
        event = MidiEvent('on', 9, 38, 127, 1)
        session.pending.put(event)
        def correct():
            session.pending.put(event)  # Arrived while settings were being restored.
            return True
        session.guard.ensure.side_effect = correct
        self.assertEqual(session.drain(), [])
        self.assertTrue(session.input_reset)
        self.assertTrue(session.pending.empty())
        session.guard.ensure.side_effect = None
        session.guard.ensure.return_value = False
        session.pending.put(event)
        self.assertEqual(session.drain(), [event])


if __name__ == '__main__':
    unittest.main()
