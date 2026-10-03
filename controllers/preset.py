"""JP MINI preset guard, derived from static inspection of KuSuite 5.3.

Source: https://www.kuwee.cn/download (manufacturer's KuSuite editor).
See docs/jpmini-protocol.md for functions, packet format, and hardware evidence.
No KuSuite source code is included.
"""
import queue
import time


def suite_message(command, payload=b''):
    packet = bytes([0x78, 0x59, command]) + len(payload).to_bytes(3, 'little')
    packet += payload + bytes([~sum(payload) & 255])
    output = bytearray([0xf0])
    accumulator = bits = 0
    for byte in packet:
        accumulator |= byte << bits
        bits += 8
        while bits >= 7:
            output.append(accumulator & 127)
            accumulator >>= 7
            bits -= 7
    if bits:
        output.append(accumulator & 127)
    output.append(0xf7)
    return bytes(output)


class SettingsDecoder:
    """Reassemble raw AE42 replies, tolerating lost/duplicate fragments."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 6:
            if self.buffer[:2] != b'\x78\x59':
                del self.buffer[0]
                continue
            size = int.from_bytes(self.buffer[3:6], 'little')
            if size > 4096:
                del self.buffer[0]
                continue
            if len(self.buffer) < size + 7:
                return
            packet = bytes(self.buffer[:size + 7])
            payload = packet[6:-1]
            if packet[-1] != (~sum(payload) & 255):
                del self.buffer[0]
                continue
            del self.buffer[:size + 7]
            if packet[2] == 0x50 and len(payload) == 32 and payload[3] < 8:
                yield payload


class PresetGuard:
    """Called only by the device I/O worker, between complete LED frames."""
    def __init__(self, send, stop, log=print):
        self.send, self.stop, self.log = send, stop, log
        self.decoder = SettingsDecoder()
        self.replies = queue.Queue(maxsize=16)

    def receive(self, data):
        # Runs on the notification thread; never writes Bluetooth or game state.
        for payload in self.decoder.feed(data):
            try:
                self.replies.put_nowait(payload)
            except queue.Full:
                pass

    def read(self):
        while True:
            try:
                self.replies.get_nowait()
            except queue.Empty:
                break
        self.send(suite_message(0x66))
        deadline = time.monotonic() + 3
        while not self.stop.is_set() and time.monotonic() < deadline:
            try:
                return self.replies.get(timeout=.1)
            except queue.Empty:
                pass
        raise RuntimeError('Could not verify JP MINI preset; input paused')

    def ensure(self):
        before = self.read()
        if before[3] == 0:
            return False
        desired = bytearray(before)
        desired[3] = 0
        self.send(suite_message(0x50, bytes(desired)))
        # The write acknowledgement also uses 0x50; discard it before requesting
        # a fresh readback, rather than treating an acknowledgement as proof.
        if self.stop.wait(.1):
            raise RuntimeError('Stopping')
        after = self.read()
        if after != bytes(desired):
            raise RuntimeError('JP MINI preset correction did not verify; input paused')
        self.log(f'Restored Preset 1 from Preset {before[3] + 1}')
        return True
