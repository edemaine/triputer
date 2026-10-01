# Triputer

Games and interactions for a screen-free computer for my ~1-year-old babies
(named after my triplets and [tricorders](https://en.wikipedia.org/wiki/Tricorder)),
using MIDI controllers, sound, and lights controlled by a Raspberry Pi.

## Hardware

The idea is to build a nontraditional computer that is safe for babies to
explore, with no screen or keyboard, which we can customize into a million
different toys and games, instead of relying on what's commercially available.

| Category | My specific setup |
| --- | --- |
| Raspberry Pi as the computer, inaccessible to the babies | Raspberry Pi 5 |
| Bluetooth MIDI controllers for button input and light output | A few [JP MINI](https://www.amazon.com/dp/B0GTT3MG66)s and a [Synido TempoKEY K32 Play](https://www.amazon.com/dp/B0GRVDV48X) |
| Bluetooth speakers for audio output | — |
| RGB LED matrix for more visual output | Two [Adafruit 64×32 RGB LED matrices](https://www.adafruit.com/product/2277) |

See [Setup](docs/setup.md) for Pi installation, Bluetooth pairing,
upload instructions, and the pad-color diagnostic.

## Fill

Fill the grid one color at a time. Every pad starts black; tapping it turns it
white. White pads stay white until all 16 match. Then each new tap can turn a
white pad blue, and so on. Each pad advances at most once per round; holding
or repeatedly tapping an already-filled pad cannot skip ahead.

The sequence alternates contrasting colors: **black → white → blue → yellow
→ magenta → green → red → cyan → black**, then repeats. Returning to black is
another round to fill, not an automatic reset.

Completing a round earns a short victory lap: the whole grid pulses, a trail
travels around the edge and spirals into the center, then the grid pulses again.
All three phases use the previous round's color against the completed color
(so completing white produces black pulses and a dark trail). After about 3.25 seconds it settles
back to the completed color. Taps during the
celebration are ignored; release and tap again to start filling the next color.
The effect respects `--brightness` and plays even when the completed color is black.

```sh
./scripts/upload
./scripts/pi -t 'cd ~/triputer && python3 interactions/fill.py'
```

Each controller progresses independently. Bank changes and reconnections keep
its progress during the same run; restarting starts black again. Stop other LED
interactions first. Ctrl+C stops and clears the LEDs. Module execution works
with `python3 -m interactions.fill`; options are the same as Coloring below.

## Coloring

Draw 4×4 pixel art by tapping pads. Every pixel starts black. Each tap advances
one step through **red → orange → yellow → green → cyan → blue → purple → pink
→ white → black**. Holding a pad or changing pressure does not cycle it again.

```sh
./scripts/upload
./scripts/pi -t 'cd ~/triputer && python3 interactions/coloring.py'
```

Each controller has its own canvas. Switching banks keeps the same drawing and
physical pad positions. Drawings survive Bluetooth reconnections during the
same run; restarting the program starts fresh. Ctrl+C stops and clears the LEDs.
Stop any other LED interaction before running coloring.

Module execution also works: `python3 -m interactions.coloring`. Options include
`--brightness`, `--address` (repeat for multiple controllers), `--base-note`,
`--fps`, `--chunk-delay`, and `--duration`, as with ripples.

## Color ripples

Press pads on a JP MINI to send rings of color across its 4×4 grid.
After completing setup, run from your development computer:

```sh
./scripts/upload
./scripts/pi -t 'cd ~/triputer && python3 -m interactions.ripple'
```

You can also run it directly with `python3 interactions/ripple.py` on the Pi.

On startup, pads stay dark until the first press identifies the active bank.
Then each pad shows its source color dimly. Press and hold to emit expanding rings;
multiple held pads emit independently and their colors blend. Releasing a pad
stops new rings from that source and fades its existing rings over two seconds.
Rapid taps on the same pad resume its ripple rhythm while it is fading, rather
than stacking wave trains that wash out the animation. After it fully fades,
the next press starts a new ripple.

Aftertouch does not start additional rings. Rows run bottom-to-top. Notes
4–19, 36–51, 52–67, and 68–83 map to the same physical pads automatically. Each range
selects a different color palette, detected on the first pad press after a bank
change. This palette remains after ripples fade and is remembered across
reconnections during the same run. Existing rings retain their colors as they
fade. For a custom consecutive note range, use `--base-note N` with the
bottom-left pad's note. Unmapped notes print a diagnostic.

All paired JP MINIs run independently, including controllers paired while the
program is running. Each controller's input and lights are matched by Bluetooth
address, so identical names and changing ALSA port numbers are harmless. A lost
connection retries automatically, with delays increasing from 2 to 15 seconds;
other controllers keep running. Reconnection resets held pads and old ripples
so missed releases cannot leave a source stuck on. Press a pad again to resume.

Press Ctrl+C to stop and clear the lights. `-t` gives the remote process a
terminal so Ctrl+C reaches it. Run only one LED program at a time.

```sh
./scripts/pi -t 'cd ~/triputer && python3 -m interactions.ripple --speed 2 --period 1.5 --fade 3'
./scripts/pi 'cd ~/triputer && python3 -m interactions.ripple --duration 30'
```

`--speed` is measured in pads per second; `--period` and `--fade` are in seconds.
The program also accepts `--brightness`, `--fps`, and `--chunk-delay`.
To restrict it to specific paired controllers, pass
`--address AA:BB:CC:DD:EE:FF`, repeating the option for additional addresses.
Press/release coordinates and connection status print to the terminal. The
ripple program reads BLE MIDI directly and does not need `--midi-port` or an
ALSA MIDI bridge.
