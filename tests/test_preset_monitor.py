"""Decode actual JP MINI settings replies, including fragmented notifications."""
import unittest

from controllers.preset import SettingsDecoder


REPLY = bytes.fromhex(
    '7859502000007800010003033232016e19191919191919191919191919'
    '191919017f229a5e0083')


class SettingsDecoderTests(unittest.TestCase):
    def test_hardware_reply_at_every_split(self):
        for split in range(len(REPLY) + 1):
            decoder = SettingsDecoder()
            got = list(decoder.feed(REPLY[:split])) + list(decoder.feed(REPLY[split:]))
            self.assertEqual(got, [REPLY[6:-1]])

    def test_duplicate_fragment_and_bad_checksum_recover(self):
        decoder = SettingsDecoder()
        corrupted = REPLY[:-1] + bytes([REPLY[-1] ^ 1])
        stream = corrupted + REPLY[:30] + REPLY[:30] + REPLY[30:]
        self.assertEqual(list(decoder.feed(stream)), [REPLY[6:-1]])

    def test_preset_is_one_based(self):
        packet = bytearray(REPLY)
        packet[9] = 7
        packet[-1] = ~sum(packet[6:-1]) & 255
        self.assertEqual(list(SettingsDecoder().feed(packet)), [bytes(packet[6:-1])])


if __name__ == '__main__':
    unittest.main()
