# JP MINI preset detection

The active preset can be queried through the same vendor Bluetooth service
used for LEDs. Note numbers alone are ambiguous across presets.

Derived from static inspection of **KuSuite 5.3** (`libKuSuite.so`), available
through the [manufacturer's download page](https://www.kuwee.cn/download).
No editor code is included here.

## Read current settings

- Service: `AE40`; write characteristic: `AE41`; notify characteristic: `AE42`
  (Bluetooth base UUID suffix `-0000-1000-8000-00805f9b34fb`).
- Subscribe to AE42 before querying.
- Read-settings suite packet: `78 59 66 00 00 00 FF`.
- Encode the packet as an LSB-first 7-bit stream, enclosed by `F0` and `F7`.
  The resulting AE41 write is `F0 78 32 19 03 00 00 40 7F F7`.
- AE42 replies contain **raw suite bytes**, without the SysEx wrapper or
  7-bit encoding. Notifications can split a packet.
- Reply command `50`, payload length 32. Payload byte 2 is the bank index;
  payload byte 3 is the preset index (0 means Preset 1).
- Packet framing: `78 59 command length[3, little endian] payload checksum`.
  Checksum is `~sum(payload) & 255`.

Evidence in the editor: `CTOOL::g_arrCmdData + 0x3d` contains the read request;
`cDlgJpmini::timerEvent` sends it. `on_receive_suite_sysex` and
`recover_setting_ui` decode the bank and preset fields. `get_JMP_data` requests
a preset's configuration using command `67` with one preset-index byte; this
configuration-read command has not yet been tested on hardware.

## Diagnostic

With a JP MINI connected, run on the Pi:

```sh
python3 -m diagnostics.preset_monitor
# Optional restrictions and timeout:
python3 -m diagnostics.preset_monitor --device AA:BB:CC:DD:EE:FF --duration 60
```

The diagnostic only queries settings and prints changes; it does not select,
save, or overwrite presets. It attaches to currently connected devices and
does not reconnect them. Ctrl+C stops it.

It can run alongside the web server for diagnosis, but concurrent queries and
LED writes can interrupt each other's packets. Production integration should
serialize queries and LED frames in the existing device worker. A query timeout
must leave the preset unknown rather than guessing from overlapping notes.

Hardware testing confirmed valid settings responses for Preset 1 and, after a
physical preset switch, Preset 2 (both with bank index 1). This distinguishes
presets independently of pad notes. Games restore Preset 1 rather than mapping
arbitrary preset layouts.

## Select the active preset

KuSuite's `toggle_preset_question` updates settings payload byte 3 and calls
`send_system_setting`, which sends command `50` with the 32-byte settings
payload. Read fresh settings with `66`, change only byte 3 (0–7 for Presets
1–8), and send the updated payload with the usual checksum and SysEx wrapping.
Read settings again to verify the result. Do not construct the other settings
from defaults or reuse stale values.

Hardware testing successfully selected Preset 2, then restored Preset 1.
Readback matched all 32 requested bytes in both cases. The server's LED output
was briefly paused during the test to prevent interleaved packets; gameplay
state was retained. No preset contents were uploaded and no save command was
sent. Persistence across a power cycle has not been tested.

The automatic guard in `controllers/preset.py` restores Preset 1 on connection,
before delivering input batches, and during idle polling. The device worker
serializes settings transactions with complete LED frames. Correction uses a
fresh settings read, modifies only the preset byte, then verifies all settings
with another read. Failed verification triggers the usual per-device retry.
Input from a correction is discarded and held pads are cleared on the engine
thread without resetting game progress. The LED diagnostic does not use this
guard.

Live server testing confirmed automatic restoration from Preset 2 both during
connection setup and while the interaction was running. After correction, the
controller reported ready with no error; the running-session correction also
appeared in the web activity log.

Because the controller does not attach a preset identifier to individual MIDI
notes, a switch concurrent with a settings query cannot be made fully atomic.
The guard verifies each collected batch before dispatch, rather than relying
solely on periodic checks. No preset contents are rewritten or save command sent.
