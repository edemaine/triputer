"""Tests for reconnect isolation, device selection, and BLE MIDI input."""

import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from controllers.bluetooth import BleMidiDecoder, BluetoothSession, BusPool, paired_controllers, GioBus
from controllers.bluetooth import CHARACTERISTIC, DEVICE
from controllers.midi import MidiEvent
from engine.devices import DeviceWorker


class BleMidiTests(unittest.TestCase):
    def test_batched_notes_running_status_pressure_and_release(self):
        events = BleMidiDecoder().decode(bytes([
            0x80, 0x80, 0x99, 4, 100, 5, 110,
            0x81, 0xA9, 4, 75, 0x82, 0x89, 4, 0,
        ]), 1)
        self.assertEqual([(e.kind, e.note, e.value) for e in events], [
            ("on", 4, 100), ("on", 5, 110), ("pressure", 4, 75), ("off", 4, 0),
        ])
        self.assertTrue(all(e.channel == 9 and e.time == 1 for e in events))

    def test_zero_velocity_and_unrelated_short_messages(self):
        events = BleMidiDecoder().decode(bytes([
            0x81, 0x82, 0xC9, 10, 0x83, 0xD9, 64, 0x84, 0x99, 9, 0,
        ]), 0)
        self.assertEqual(events, [MidiEvent("off", 9, 9, 0, 0)])

    def test_sysex_continuation_and_real_time(self):
        decoder = BleMidiDecoder()
        self.assertEqual(decoder.decode(bytes([0x80, 0x80, 0xF0, 1, 2]), 0), [])
        events = decoder.decode(bytes([
            0x81, 3, 4, 0x81, 0xF8, 5, 0x82, 0xF7, 0x83, 0x99, 4, 100,
        ]), 1)
        self.assertEqual(events, [MidiEvent("on", 9, 4, 100, 1)])
        events = decoder.decode(bytes([0x80, 0x80, 0x99, 4, 0x81, 0xF8, 100]), 2)
        self.assertEqual(events, [MidiEvent("on", 9, 4, 100, 2)])

    def test_malformed_packets_and_running_status_reset(self):
        decoder = BleMidiDecoder()
        decoder.decode(bytes([0x80, 0x80, 0x99, 4, 100]), 0)
        for data in (b"", b"\x80", b"\x00\x80", b"\x80\x80", b"\x80\x80\x99\x04", b"\x80\x80\x04\x64"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                decoder.decode(data, 1)

    def test_only_paired_jp_minis_are_selected_independent_of_connection(self):
        objects = {
            "a": {DEVICE: {"Address": "AA", "Name": "JP-Mini", "Paired": True, "Connected": False}},
            "b": {DEVICE: {"Address": "BB", "Name": "JP-Mini", "Paired": True, "Connected": True}},
            "c": {DEVICE: {"Address": "CC", "Name": "JP-Mini", "Paired": False}},
            "d": {DEVICE: {"Address": "DD", "Name": "Speaker", "Paired": True}},
        }
        self.assertEqual(paired_controllers(objects), {"AA", "BB"})


class FastStop:
    def __init__(self):
        self.stopped = False
        self.delays = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.delays.append(seconds)
        return self.stopped


class ReconnectTests(unittest.TestCase):
    def test_notifications_and_disconnect_are_isolated_and_stale_input_is_ignored(self):
        first = BluetoothSession("AA", 0.03, Mock())
        second = BluetoothSession("BB", 0.03, Mock())
        packet = {"Value": bytes([0x80, 0x80, 0x99, 52, 100])}
        first._midi_changed(CHARACTERISTIC, packet, [])
        self.assertEqual([event.note for event in first.drain()], [52])
        self.assertEqual(list(second.drain()), [])
        first._device_changed(DEVICE, {"Connected": False}, [])
        with self.assertRaises(RuntimeError):
            list(first.drain())
        second._midi_changed(CHARACTERISTIC, packet, [])
        self.assertEqual([event.note for event in second.drain()], [52])
        second.close()
        second._midi_changed(CHARACTERISTIC, packet, [])
        self.assertTrue(second.pending.empty())

    def test_bus_connections_are_reused_and_only_disposed_at_shutdown(self):
        first, second, replacement = Mock(), Mock(), Mock()
        first.get_is_connected.return_value = True
        pool = BusPool()
        with patch("controllers.bluetooth.private_system_bus", side_effect=[first, second, replacement]):
            self.assertIs(pool.get("AA"), first)
            self.assertIs(pool.get("AA"), first)
            self.assertIs(pool.get("BB"), second)
            first.get_is_connected.return_value = False
            self.assertIs(pool.get("AA"), replacement)
            for bus in (first, second, replacement):
                bus.close.assert_not_called()
            pool.close()
            for bus in (first, second, replacement):
                bus.close.assert_called_once()

    def test_gio_bus_cannot_terminate_process_when_closed(self):
        gio = Mock()
        gio.DBusConnectionFlags.AUTHENTICATION_CLIENT = 1
        gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION = 2
        with patch.dict("sys.modules", {"gi.repository": SimpleNamespace(Gio=gio, GLib=Mock())}):
            bus = GioBus()
        bus.connection.set_exit_on_close.assert_called_once_with(False)

    def test_failed_open_and_mid_frame_disconnect_retry_and_close(self):
        sessions = []
        frames = []

        class Session:
            events = 0

            def __init__(self, address, delay):
                self.number = len(sessions)
                self.closed = False
                self.lights = SimpleNamespace(chunk_delay=0.03)
                sessions.append(self)

            def open(self, stop):
                if self.number == 0:
                    raise RuntimeError("Device asleep")

            def drain(self):
                if self.number == 1:
                    return [MidiEvent("on", 9, 4, 100, 0)]
                return []

            def send(self, colors):
                frames.append(colors)
                if self.number == 1:
                    raise RuntimeError("Not connected")
                worker.stop_event.set()

            def close(self, clear=True):
                self.closed = True

        worker = DeviceWorker("AA", lambda *args: None, Session)
        worker.stop_event = FastStop()
        worker.run()
        self.assertEqual(len(sessions), 3)
        self.assertTrue(all(s.closed for s in sessions))
        self.assertIn(2, worker.stop_event.delays)
        self.assertIn(4, worker.stop_event.delays)

    def test_slow_reconnect_does_not_block_another_controller(self):
        unblock = threading.Event()
        good_frame = threading.Event()
        workers = {}

        class Session:
            events = 0

            def __init__(self, address, delay):
                self.address = address
                self.lights = SimpleNamespace(chunk_delay=0.03)

            def open(self, stop):
                if self.address == "offline":
                    unblock.wait(2)
                    raise RuntimeError("Offline")

            def drain(self):
                return []

            def send(self, colors):
                good_frame.set()
                workers[self.address].stop_event.set()

            def close(self, clear=True):
                pass

        for address in ("offline", "online"):
            workers[address] = DeviceWorker(address, lambda *args: None, Session)
            workers[address].start()
        try:
            self.assertTrue(good_frame.wait(1), "Online controller was blocked by offline controller")
        finally:
            for worker in workers.values():
                worker.stop_event.set()
            unblock.set()
            for worker in workers.values():
                worker.join(2)



if __name__ == "__main__":
    unittest.main()
