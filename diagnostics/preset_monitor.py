#!/usr/bin/env python3
"""Read JP MINI preset/bank changes without changing its configuration.

May run alongside the web server. Queries use KuSuite's read-settings command;
no preset selection, configuration writes, or LED commands are sent.
"""
import argparse
import sys
import time
from pathlib import Path

if __name__ == '__main__' and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controllers.bluetooth import (CHARACTERISTIC, DEVICE, GioBus, managed_objects,
                                   paired_controllers, start_dbus_loop)

from controllers.preset import SettingsDecoder, suite_message

READ_SETTINGS = suite_message(0x66)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', action='append', help='Bluetooth address; repeat to select several')
    parser.add_argument('--duration', type=float, default=0, help='Seconds to monitor; 0 until Ctrl+C')
    args = parser.parse_args()
    loop, thread = start_dbus_loop()
    bus = GioBus()
    notifications, subscriptions = [], []
    try:
        objects = managed_objects(bus)
        selected = {a.upper() for a in args.device} if args.device else paired_controllers(objects)
        writers = []
        for path, interfaces in objects.items():
            props = interfaces.get(DEVICE, {})
            address = props.get('Address', '').upper()
            if address not in selected or not props.get('Connected'):
                continue
            chars = {i[CHARACTERISTIC]['UUID']: p for p, i in objects.items()
                     if p.startswith(path + '/') and CHARACTERISTIC in i}
            writer = chars.get('0000ae41-0000-1000-8000-00805f9b34fb')
            notifier = chars.get('0000ae42-0000-1000-8000-00805f9b34fb')
            if not writer or not notifier:
                continue
            def callback(interface, changed, invalidated, address=address,
                         decoder=SettingsDecoder(), last={}):
                for payload in decoder.feed(changed.get('Value', [])):
                    settings = dict(bank=payload[2], preset=payload[3] + 1)
                    if settings != last:
                        last.clear()
                        last.update(settings)
                        print(f'{address}: Preset {settings["preset"]}, bank index {settings["bank"]}', flush=True)
            subscriptions.append(bus.subscribe(notifier, callback))
            bus.call(notifier, CHARACTERISTIC, 'StartNotify')
            notifications.append(notifier)
            writers.append((address, writer, notifier))
        if not writers:
            raise RuntimeError('No selected connected JP MINI found')
        print('Reading settings about once per second; only changes are printed.', flush=True)
        deadline = time.monotonic() + args.duration if args.duration else float('inf')
        failed = set()
        while time.monotonic() < deadline:
            for address, writer, notifier in writers:
                try:
                    if address in failed:
                        bus.call(notifier, CHARACTERISTIC, 'StartNotify')
                    bus.call(writer, CHARACTERISTIC, 'WriteValue', '(aya{sv})',
                             (READ_SETTINGS, {'type': bus.GLib.Variant('s', 'command')}))
                    failed.discard(address)
                except Exception as error:
                    if address not in failed:
                        print(f'{address}: {error}; waiting for reconnection', flush=True)
                    failed.add(address)
            # Avoid repeatedly colliding with the server's 8 Hz LED writes.
            time.sleep(1.07)
    except KeyboardInterrupt:
        pass
    finally:
        for subscription in subscriptions:
            bus.unsubscribe(subscription)
        for notifier in notifications:
            try:
                bus.call(notifier, CHARACTERISTIC, 'StopNotify')
            except Exception:
                pass
        loop.quit()
        thread.join()
        bus.close()


if __name__ == '__main__':
    main()
