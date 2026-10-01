"""JP MINI pad RGB protocol, based on padmon's KuSuite reverse engineering.

Source: https://tangled.org/faz.ms/padmon/blob/main/jpmini.py
See THIRD_PARTY_LICENSES.md for the MIT license.
"""


BANK_BASES = (4, 36, 52, 68)


def note_bank(note, base_note=None):
    """Find the physical pad bank, or use an explicit custom note range."""
    bases = BANK_BASES if base_note is None else (base_note,)
    return next((base for base in bases if 0 <= note - base < 16), None)


def note_to_position(note: int, base_note: int = 4) -> tuple[int, int] | None:
    """Return (row, column), both 1-based from bottom-left, or None off-grid."""
    index = note - base_note
    if not 0 <= index < 16:
        return None
    row, column = divmod(index, 4)
    return row + 1, column + 1


def pad_colors(colors):
    """Encode 16 RGB tuples as a complete, transient pad-color SysEx frame.

    Slots run left to right, starting with the bottom row. The firmware expects
    18 GRB slots; leave the two non-pad slots black. This is command 0x51, not
    a preset save. Bluetooth sends this frame to AE41, not the MIDI service.
    """
    if len(colors) != 16:
        raise ValueError("Expected exactly 16 pad colors")
    payload = bytearray([0xFF])
    for red, green, blue in colors:
        payload.extend((green, red, blue))
    payload.extend(bytes(6))
    packet = b"\x78\x59\x51" + len(payload).to_bytes(3, "little")
    packet += payload + bytes([~sum(payload) & 0xFF])

    # Encode the complete packet as an LSB-first stream of 7-bit MIDI bytes.
    packed = bytearray([0xF0])
    accumulator = bits = 0
    for value in packet:
        accumulator |= value << bits
        bits += 8
        while bits >= 7:
            packed.append(accumulator & 0x7F)
            accumulator >>= 7
            bits -= 7
    if bits:
        packed.append(accumulator & 0x7F)
    packed.append(0xF7)
    return bytes(packed)
