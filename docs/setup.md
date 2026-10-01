# Setup

Prepare the Pi, pair controllers, and upload the programs. See the
[README](../README.md) for games and interactions, and its
[hardware table](../README.md#hardware) for the equipment used.

## Pi setup

Run the Pi headless, managing it from another computer over SSH. No keyboard or
monitor needs to be connected to the Pi.

1. Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) to install
   Raspberry Pi OS Lite (64-bit) on a microSD card, selecting Raspberry Pi 5.
2. In Imager, set the hostname to `triputer`, create a user, configure Wi-Fi
   (or use Ethernet), and enable SSH.
3. Insert the card, power on the Pi, and connect from another computer:
   `ssh yourusername@triputer.local`.
4. Run `sudo apt update`, then `sudo apt full-upgrade`, then `sudo reboot`.

See the [official setup guide](https://www.raspberrypi.com/documentation/computers/getting-started.html).

## MIDI controller setup

Run these commands on the Pi over SSH. First connect the controller over
Bluetooth, then check that MIDI events reach applications.

### Power on the controller

The **JP MINI enters pairing mode automatically on initial boot**, with no
button presses needed. For other controllers, follow their pairing instructions.

Power on one controller at a time so it is easy to identify. Disconnect it from
any phone or other computer that might reconnect automatically.

The [linked FCC manual](https://fccid.io/2A6PB-JP-1/User-Manual/User-Manual-8798278)
is for the **JP-1**, the larger model with eight knobs. Its instruction to hold
**BT** to toggle wireless on/off is not needed for initial JP MINI pairing.

### Discover the controller

Install the Bluetooth tools and MIDI diagnostic utility:

```sh
sudo apt install bluez alsa-utils
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth
bluetoothctl power on
```

At the shell, scan for 30 seconds, saving the noisy device updates to a log,
then print a stable list of known devices:

```sh
bluetoothctl --timeout 30 scan on > /tmp/triputer-bluetooth-scan.log 2>&1
bluetoothctl devices
```

To filter that list by likely controller names:

```sh
bluetoothctl devices | grep -iE 'jp|mini|midi'
```

If nothing matches, check the full list for an unexpected name. Avoid filtering
scan output to `[NEW]`: previously discovered devices may produce only `[CHG]`
updates. If discovery fails, inspect `/tmp/triputer-bluetooth-scan.log` for errors.
This timed scan identifies the address; rediscover the device in the interactive
session below before pairing, since an unpaired device can disappear afterward.

### Pair and connect

Keep the controller powered on and disconnected from other computers. Open the
interactive Bluetooth prompt:

```sh
bluetoothctl
```

Replace `AA:BB:CC:DD:EE:FF` below with the address from `devices`. First start
discovery in this same session, filtering by the address to reduce noise:

```text
agent on
default-agent
menu scan
clear
pattern AA:BB:CC:DD:EE:FF
back
scan on
```

Wait for the controller to reappear, then leave scanning running while pairing.
Repeated `[CHG] Device ... RSSI` lines for its address count: they are signal
strength updates, not errors. The address filter still allows these updates.
Type or paste the pairing command and press Enter while they scroll;
`bluetoothctl` still accepts commands. There is no need to wait for silence.
Run each command separately and wait for its response. Accept a pairing
confirmation if prompted for this controller.

```text
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
info AA:BB:CC:DD:EE:FF
scan off
quit
```

`pair` establishes pairing, `trust` allows future connections without further
authorization, and `connect` opens the connection. In `info`, check for
`Paired: yes`, `Trusted: yes`, and `Connected: yes`.

If a command reports `Device ... not available`, BlueZ does not currently have
that device available. Keep scanning, power-cycle the pad if needed, and ensure
another computer has not connected to it. Retry only after it reappears. If its
address changes, use the newly discovered address. The
[scan pattern filter](https://github.com/bluez/bluez/blob/master/doc/bluetoothctl-scan.rst)
can also match a name prefix such as `JP-Mini` instead of an address.

Some BLE MIDI devices connect without conventional pairing. If `pair` fails,
try `connect` and inspect `info` for `Connected: yes`. Record any errors if it
does not connect. See the [BlueZ command reference](https://github.com/bluez/bluez/blob/master/doc/bluetoothctl.rst).

### Check MIDI input

Back at the shell, list available ALSA MIDI input ports:

```sh
aseqdump -l
```

If the controller appears, use its port number (for example, `128:0`):

```sh
aseqdump -p 128:0
```

Press pads and look for MIDI events; press Ctrl+C to stop. This only monitors
input and does not produce sound.

If Bluetooth connects but no MIDI port appears, the Pi may need a BLE MIDI
backend or bridge. OS Lite may not include one; modern
[PipeWire/WirePlumber supports Bluetooth MIDI](https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration/bluetooth.html).
Backend setup depends on the OS version. To diagnose missing MIDI ports, check
`cat /etc/os-release`, `bluetoothctl info AA:BB:CC:DD:EE:FF`, and `aseqdump -l`.
A USB data cable provides an alternative for testing the controller with the
same MIDI commands.

## Develop and upload

Run the local commands below from the project root. Install the Python
dependencies on the Pi:

```sh
sudo apt install python3-dbus python3-gi
```

The hardware test uses Python 3 on the Pi. The local helper scripts require a
POSIX shell with `ssh` and `scp` (for example, Cygwin Bash on Windows).

Copy `.env.example` to `.env` at the project root and set the SSH destination:

```sh
cp .env.example .env
# Edit .env: PI=yourusername@triputer.local
```

`PI` can also be an SSH config alias. `.env` is ignored by Git and is never
included in the default upload. Without `.env`, the scripts use an exported
`PI` environment variable.

```sh
./scripts/pi                              # Open a Pi shell
./scripts/pi 'hostname'                   # Run a command on the Pi
./scripts/upload                          # Copy programs and docs to ~/triputer
./scripts/upload diagnostics controllers  # Copy selected directories
```

### Test pad colors

On the Pi, install the dependency and MIDI tools, then pair and connect one
JP MINI using the instructions above:

```sh
sudo apt install python3-dbus alsa-utils
```

From your development computer:

```sh
./scripts/upload
./scripts/pi 'cd ~/triputer && python3 -m diagnostics.led_test'
```

The test walks through the 16 pads, displays red/green/blue, runs two gentle
fades, then holds an RGB pattern for ten seconds. Press, hold, and release pads
throughout: MIDI events print alongside the animation stages. Check the physical
colors and pad order, and whether pressing a pad overrides its displayed color.
The test clears the lights on completion or Ctrl+C; it does not save a preset or
disconnect Bluetooth.

Defaults are 25% brightness and a target of eight full-grid updates per second.
To adjust the test, or select among multiple controllers:

```sh
./scripts/pi 'cd ~/triputer && python3 -m diagnostics.led_test --help'
./scripts/pi 'cd ~/triputer && python3 -m diagnostics.led_test --brightness 0.15 --hold 30'
./scripts/pi 'cd ~/triputer && python3 -m diagnostics.led_test --address AA:BB:CC:DD:EE:FF --midi-port 128:0'
```

Choose the MIDI port from `aseqdump -l`; port numbers can change. Do not assume
the pad note numbers match a particular preset or bank.

MIDI output includes each note's row and column, numbered 1–4 from the
bottom-left: rows increase upward and columns increase to the right. The default
mapping uses notes 4–19 in row order (4 = bottom-left, 7 = bottom-right,
16 = top-left, 19 = top-right). Use `--base-note N` for a preset with a different
starting note. Notes outside the configured 16-note range are marked as unmapped.

LED output uses the JP MINI's vendor Bluetooth service AE40, characteristic
AE41, alongside the normal MIDI input. The encoder follows
[padmon's reverse-engineered protocol](https://tangled.org/faz.ms/padmon), with
its license in [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). Each grid is
sent in 20-byte chunks spaced 30 ms apart. `--chunk-delay` can adjust that pacing;
faster host writes do not guarantee that the controller displays every frame.

## Run an interaction

Once setup is complete, try [coloring](../README.md#coloring) or
[color ripples](../README.md#color-ripples). Both read BLE MIDI directly;
the ALSA MIDI check above is a diagnostic and is not required for either game.

## Development checks

Code is organized into `controllers/` for hardware support, `interactions/`
for games, `diagnostics/` for hardware tests, and `tests/` for automated tests.
Shell helpers live in `scripts/`. Run Python entry points from the project root
with `python3 -m interactions.ripple` or `python3 -m diagnostics.led_test`.
Direct execution also works: `python3 interactions/ripple.py` or
`python3 diagnostics/led_test.py`. When using a script path, you can run from
any directory; imports are resolved relative to the script.

All interactions share connection management in `interactions/runtime.py` and
the color encoder in `controllers/jpmini.py`. The LED test uses
`controllers/midi.py` and `controllers/bluetooth.py`; ripples use GIO's thread-safe D-Bus connections in
`controllers/bluetooth.py` for address-specific input and output. To check
animation, bank mapping, input parsing, and reconnection without hardware:

```sh
python3 -m unittest discover -s tests -v
```
