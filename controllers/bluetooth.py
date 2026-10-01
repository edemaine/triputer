"""Address-specific BLE MIDI input and reconnectable JP MINI sessions.

Unlike ALSA display names and client numbers, a Bluetooth address identifies
the same controller after reconnection and among multiple identical devices.
"""

import queue
import threading
import time

from .midi import MidiEvent
from .jpmini import pad_colors

DEVICE = "org.bluez.Device1"
SERVICE = "org.bluez.GattService1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
UUID_BASE = "-0000-1000-8000-00805f9b34fb"
BLACK = [(0, 0, 0)] * 16

MIDI_SERVICE = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHARACTERISTIC = "7772e5db-3868-4112-a1a9-f2669d106bf3"
PROPERTIES = "org.freedesktop.DBus.Properties"


def paired_controllers(objects):
    """Only automatically use previously paired JP MINIs, never nearby strangers."""
    return {
        str(device["Address"]).upper()
        for interfaces in objects.values()
        if (device := interfaces.get(DEVICE, {})).get("Paired")
        and str(device.get("Name", "")).lower() == "jp-mini"
    }


class BleMidiDecoder:
    """Extract pad messages from BLE MIDI 1.0; ignore other MIDI messages.

    Consume timestamps, packet-local running status, and system messages, but
    use local arrival time for this immediate visual interaction. SysEx can
    span packets; other MIDI messages cannot. No SysEx payload is retained.
    Format: https://microsoft.github.io/MIDI/kb/ble-midi-transport-architecture/
    """

    def __init__(self):
        self.sysex = False

    def decode(self, packet, now):
        data = bytes(packet)
        if len(data) < 2 or data[0] & 0xC0 != 0x80:
            raise ValueError("Invalid BLE MIDI header")
        events = []
        running = None
        i = 1
        while i < len(data):
            if data[i] & 0x80:
                i += 1  # timestampLow, including timestamp value zero (0x80)
                if i == len(data):
                    raise ValueError("Timestamp without MIDI data")
            elif self.sysex:
                i += 1
                continue
            elif running is None:
                raise ValueError("MIDI data without status")

            if data[i] & 0x80:
                status = data[i]
                i += 1
                if status >= 0xF8:
                    continue  # Real-time messages preserve running status/SysEx.
                if status == 0xF0:
                    self.sysex = True
                    running = None
                    continue
                if status == 0xF7:
                    self.sysex = False
                    running = None
                    continue
                if self.sysex:
                    raise ValueError("Unexpected status inside SysEx")
                running = status if status < 0xF0 else None
            else:
                if self.sysex:
                    continue
                if running is None:
                    raise ValueError("Running status without a channel message")
                status = running

            family = status >> 4
            if status < 0xF0:
                count = 1 if family in (0xC, 0xD) else 2
            else:
                count = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0}.get(status)
                if count is None:
                    raise ValueError("Unsupported MIDI status")
            values = []
            while len(values) < count:
                if i >= len(data):
                    raise ValueError("Truncated MIDI message")
                if data[i] & 0x80:
                    # A timestamped real-time message can interrupt MIDI data.
                    if i + 1 < len(data) and data[i + 1] >= 0xF8:
                        i += 2
                        continue
                    raise ValueError("Unexpected status in MIDI data")
                values.append(data[i])
                i += 1
            if family in (0x8, 0x9, 0xA):
                kind = {0x8: "off", 0x9: "on", 0xA: "pressure"}[family]
                if kind == "on" and values[1] == 0:
                    kind = "off"
                events.append(MidiEvent(kind, status & 0xF, values[0], values[1], now))
        return events


def start_dbus_loop():
    """Start notification dispatch once, before creating worker bus connections."""
    try:
        from gi.repository import GLib
    except ImportError:
        raise RuntimeError("Install the dependency: sudo apt install python3-gi") from None
    loop = GLib.MainLoop()
    thread = threading.Thread(target=loop.run, name="bluetooth-notifications", daemon=True)
    thread.start()
    return loop, thread


def managed_objects(bus):
    return bus.call("/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")[0]


def private_system_bus():
    return GioBus()


class GioBus:
    """GIO supports concurrent calls and notification dispatch on a connection.

    https://docs.gtk.org/gio/class.DBusConnection.html
    No dbus-python/libdbus objects enter the ripple process.
    """

    def __init__(self):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.connection = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None,
        )
        self.connection.set_exit_on_close(False)

    def call(self, path, interface, method, signature=None, values=(), timeout=5):
        parameters = self.GLib.Variant(signature, values) if signature else None
        result = self.connection.call_sync(
            "org.bluez", path, interface, method, parameters, None,
            self.Gio.DBusCallFlags.NONE, int(timeout * 1000), None,
        )
        return result.unpack()

    def subscribe(self, path, callback):
        def changed(connection, sender, object_path, interface, signal, parameters, user_data):
            callback(*parameters.unpack())
        return self.connection.signal_subscribe(
            "org.bluez", PROPERTIES, "PropertiesChanged", path, None,
            self.Gio.DBusSignalFlags.NONE, changed, None,
        )

    def unsubscribe(self, subscription):
        self.connection.signal_unsubscribe(subscription)

    def get_is_connected(self):
        return not self.connection.is_closed()

    def close(self):
        if self.get_is_connected():
            self.connection.close_sync(None)


class BusPool:
    """Reuse one connection per device; dispose only after dispatch has stopped.

    Reconnect the Bluetooth device without replacing its D-Bus connection.
    """

    def __init__(self):
        self.connections = {}
        self.retired = []
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            bus = self.connections.get(key)
            if bus is None or not bus.get_is_connected():
                if bus is not None:
                    self.retired.append(bus)
                bus = private_system_bus()
                self.connections[key] = bus
            return bus

    def close(self):
        for bus in [*self.connections.values(), *self.retired]:
            bus.close()
        self.connections.clear()
        self.retired.clear()


class BluetoothSession:
    """Own subscriptions and queues for one address, without owning pairing."""

    def __init__(self, address, chunk_delay, bus):
        self.address = address.upper()
        self.chunk_delay = chunk_delay
        self.bus = bus
        self.lights = self.midi = None
        self.matches = []
        self.subscribed = False
        self.lost = threading.Event()
        self.pending = queue.Queue(maxsize=1024)
        self.decoder = BleMidiDecoder()
        self.events = 0
        self.packet_errors = 0

    def open(self, stop):
        objects = managed_objects(self.bus)
        matches = [
            (path, interfaces[DEVICE]) for path, interfaces in objects.items()
            if DEVICE in interfaces
            and str(interfaces[DEVICE].get("Address", "")).upper() == self.address
        ]
        if len(matches) != 1:
            raise RuntimeError("Device not known yet; pair it with bluetoothctl")
        path, props = matches[0]
        if not props.get("Paired"):
            raise RuntimeError("Device is not paired; pair it with bluetoothctl")
        if stop.is_set():
            raise RuntimeError("Stopping")
        if not props.get("Connected"):
            self.bus.call(path, DEVICE, "Connect", timeout=8)
        deadline = time.monotonic() + 6
        while not stop.is_set():
            props = self.bus.call(path, PROPERTIES, "GetAll", "(s)", (DEVICE,))[0]
            if not props.get("Connected"):
                raise RuntimeError("Not connected")
            if props.get("ServicesResolved"):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Bluetooth services have not resolved yet")
            stop.wait(0.2)
        if stop.is_set():
            raise RuntimeError("Stopping")

        objects = managed_objects(self.bus)
        services = {
            p for p, interfaces in objects.items()
            if interfaces.get(SERVICE, {}).get("Device") == path
            and str(interfaces[SERVICE].get("UUID", "")).lower() == MIDI_SERVICE
        }
        midi_paths = [
            p for p, interfaces in objects.items()
            if interfaces.get(CHARACTERISTIC, {}).get("Service") in services
            and str(interfaces[CHARACTERISTIC].get("UUID", "")).lower() == MIDI_CHARACTERISTIC
        ]
        if len(midi_paths) != 1:
            raise RuntimeError("BLE MIDI characteristic is unavailable")
        midi_path = midi_paths[0]
        self.matches.append(self.bus.subscribe(path, self._device_changed))
        self.matches.append(self.bus.subscribe(midi_path, self._midi_changed))
        self.midi = midi_path
        self.bus.call(midi_path, CHARACTERISTIC, "ReadValue", "(a{sv})", ({},))
        self.bus.call(midi_path, CHARACTERISTIC, "StartNotify")
        self.subscribed = True
        self.lights = GioPadLights(self.bus, path, objects, self.chunk_delay)
        if self.lost.is_set():
            raise RuntimeError("Disconnected while setting up MIDI")

    def _device_changed(self, interface, changed, invalidated):
        if interface == DEVICE and (
            ("Connected" in changed and not changed["Connected"])
            or ("ServicesResolved" in changed and not changed["ServicesResolved"])
        ):
            self.lost.set()

    def _midi_changed(self, interface, changed, invalidated):
        if interface != CHARACTERISTIC or "Value" not in changed or self.lost.is_set():
            return
        if not changed["Value"]:
            return  # The BLE MIDI initial read normally returns an empty value.
        try:
            events = self.decoder.decode(changed["Value"], time.monotonic())
        except ValueError as error:
            self.decoder = BleMidiDecoder()
            self.packet_errors += 1
            if self.packet_errors == 1:
                print(f"[{self.address}] Ignoring malformed MIDI packet: {error}", flush=True)
            return
        self.events += len(events)
        for event in events:
            if event.kind == "pressure":
                continue  # Ripples need edges only; avoid queueing pressure floods.
            try:
                self.pending.put_nowait(event)
            except queue.Full:
                # A lost release could leave a source held forever. Rebuild the
                # session and reset animation instead of silently dropping it.
                self.lost.set()
                return

    def drain(self):
        if self.lost.is_set():
            raise RuntimeError("Bluetooth disconnected or MIDI input needs resynchronization")
        while True:
            try:
                yield self.pending.get_nowait()
            except queue.Empty:
                return

    def send(self, colors):
        if self.lost.is_set():
            raise RuntimeError("Bluetooth disconnected")
        self.lights.send(colors)

    def close(self, clear=True):
        # Unregister callbacks first so stale notifications cannot reach the next
        # session. Do not disconnect the device or remove its pairing/trust.
        self.lost.set()  # Ignore callbacks already queued before unsubscribe.
        for match in self.matches:
            self.bus.unsubscribe(match)
        self.matches.clear()
        if self.bus is not None:
            try:
                if self.lights is not None:
                    try:
                        self.lights.close(clear=clear)
                    except Exception:
                        pass  # An offline device cannot accept a final black frame.
                if self.subscribed:
                    try:
                        self.bus.call(self.midi, CHARACTERISTIC, "StopNotify", timeout=3)
                    except Exception:
                        pass
            finally:
                self.lights = self.midi = None
                self.subscribed = False


class GioPadLights:
    """The shared pad-color encoder, written through the thread-safe GIO bus."""

    def __init__(self, bus, device, objects, chunk_delay):
        self.bus, self.chunk_delay = bus, chunk_delay
        services = {
            path for path, interfaces in objects.items()
            if interfaces.get(SERVICE, {}).get("Device") == device
            and interfaces[SERVICE].get("UUID", "").lower() == "0000ae40" + UUID_BASE
        }
        chars = {
            props["UUID"].lower(): (path, props)
            for path, interfaces in objects.items()
            if (props := interfaces.get(CHARACTERISTIC, {})).get("Service") in services
        }
        writer = chars.get("0000ae41" + UUID_BASE)
        if not writer or "write-without-response" not in writer[1].get("Flags", []):
            raise RuntimeError("Pad-color endpoint AE41 is unavailable")
        self.writer = writer[0]
        self.notifier = None
        notify = chars.get("0000ae42" + UUID_BASE)
        if notify:
            bus.call(notify[0], CHARACTERISTIC, "StartNotify")
            self.notifier = notify[0]

    def send(self, colors):
        frame = pad_colors(colors)
        options = {"type": self.bus.GLib.Variant("s", "command")}
        for start in range(0, len(frame), 20):
            self.bus.call(self.writer, CHARACTERISTIC, "WriteValue", "(aya{sv})",
                          (frame[start:start + 20], options), timeout=3)
            time.sleep(self.chunk_delay)

    def close(self, clear=True):
        try:
            if clear:
                self.send(BLACK)
        finally:
            if self.notifier is not None:
                self.bus.call(self.notifier, CHARACTERISTIC, "StopNotify", timeout=3)


class PadLights:
    """Attach to an existing BlueZ connection; never disconnect MIDI on exit."""

    def __init__(self, address, chunk_delay, bus=None):
        try:
            import dbus
        except ImportError:
            raise RuntimeError("Install the dependency: sudo apt install python3-dbus") from None
        self.dbus = dbus
        self.bus = bus if bus is not None else dbus.SystemBus()
        manager = dbus.Interface(
            self.bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )
        objects = manager.GetManagedObjects()
        matches = []
        for path, interfaces in objects.items():
            device = interfaces.get(DEVICE, {})
            if not device.get("Connected"):
                continue
            if address:
                match = str(device.get("Address", "")).lower() == address.lower()
            else:
                match = str(device.get("Name", "")).lower() == "jp-mini"
            if match:
                matches.append((path, device))
        if len(matches) != 1:
            raise RuntimeError(
                f"Found {len(matches)} matching connected controllers. "
                "Connect one JP MINI, or select one with --address."
            )
        path, device = matches[0]
        if not device.get("ServicesResolved"):
            raise RuntimeError("Bluetooth services are still resolving; retry shortly.")
        services = {
            p for p, interfaces in objects.items()
            if interfaces.get(SERVICE, {}).get("Device") == path
            and str(interfaces[SERVICE].get("UUID")).lower() == "0000ae40" + UUID_BASE
        }
        chars = {}
        for p, interfaces in objects.items():
            char = interfaces.get(CHARACTERISTIC, {})
            if char.get("Service") in services:
                chars[str(char.get("UUID")).lower()] = (p, char)
        if "0000ae41" + UUID_BASE not in chars:
            raise RuntimeError("Connected device has no AE40/AE41 pad-color endpoint.")
        write_path, props = chars["0000ae41" + UUID_BASE]
        if "write-without-response" not in props.get("Flags", []):
            raise RuntimeError("AE41 does not support write-without-response.")
        self.writer = dbus.Interface(self.bus.get_object("org.bluez", write_path), CHARACTERISTIC)
        self.notifier = None
        notify = chars.get("0000ae42" + UUID_BASE)
        if notify:
            notifier = dbus.Interface(self.bus.get_object("org.bluez", notify[0]), CHARACTERISTIC)
            notifier.StartNotify(timeout=10)
            self.notifier = notifier
        self.chunk_delay = chunk_delay
        self.frames = 0
        print(f"LED output: {device.get('Name')} ({device.get('Address')}) via AE41", flush=True)

    def send(self, colors):
        frame = pad_colors(colors)
        for start in range(0, len(frame), 20):
            self.writer.WriteValue(
                self.dbus.ByteArray(frame[start:start + 20]),
                self.dbus.Dictionary({"type": "command"}, signature="sv"),
                timeout=10,
            )
            # Include a gap after the final chunk, before the next frame.
            time.sleep(self.chunk_delay)
        self.frames += 1

    def close(self, clear=True):
        try:
            if clear:
                self.send(BLACK)
        finally:
            if self.notifier is not None:
                self.notifier.StopNotify(timeout=5)
