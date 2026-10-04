"""Single-threaded game state, with queued web commands and Bluetooth events."""
from collections import Counter, deque
from concurrent.futures import Future
import json
from pathlib import Path
import queue
import re
import threading
import time
import uuid

from .devices import BLACK, BluetoothBackend
from .lock import EngineLock
from .registry import SESSION_FACTORIES, options_for


def device_ids(values):
    if values is None:
        return None
    if not isinstance(values, list) or not values or any(
        not isinstance(value, str) or not re.fullmatch(r'(?:[\dA-Fa-f]{2}:){5}[\dA-Fa-f]{2}', value)
        for value in values
    ):
        raise ValueError('Select devices by Bluetooth address')
    return list(dict.fromkeys(value.upper() for value in values))


class Engine:
    def __init__(self, backend=None, state_path=None, lock_path=None, auto_join=True):
        self.backend = backend if backend is not None else BluetoothBackend()
        self.hardware_lock = EngineLock(lock_path) if isinstance(self.backend, BluetoothBackend) else None
        self.state_path = Path(state_path) if state_path else None
        self.inbox = queue.Queue(maxsize=4096)
        self.shutdown = threading.Event()
        self.thread = None
        self.devices, self.sessions, self.assignments, self.owners = {}, {}, {}, {}
        self.auto_join = auto_join
        self._pending_all = None
        self._legacy_default = None
        self.frames, self.assigned_at = {}, {}
        self.logs = deque(maxlen=80)
        self.error = None
        self.generation = uuid.uuid4().hex
        self.overflow = threading.Event()

    def emit(self, kind, value):
        try:
            self.inbox.put_nowait((kind, value, None))
        except queue.Full:
            self.overflow.set()

    def start(self):
        if self.hardware_lock:
            self.hardware_lock.acquire()
        try:
            self._restore()
            self.backend.start(self.emit)
            self.thread = threading.Thread(target=self._loop, name='interaction-engine', daemon=True)
            self.thread.start()
        except BaseException:
            self.backend.close()
            if self.hardware_lock:
                self.hardware_lock.close()
            raise
        return self

    def call(self, command, **values):
        if self.shutdown.is_set() or self.thread is None or not self.thread.is_alive():
            raise RuntimeError('Engine is not running')
        future = Future()
        self.inbox.put((command, values, future), timeout=2)
        return future.result(timeout=15)

    def close(self):
        self.shutdown.set()
        if self.thread:
            self.thread.join()
        try:
            self.backend.close()
        finally:
            if self.hardware_lock:
                self.hardware_lock.close()

    def _log(self, message):
        if not self.logs or self.logs[-1]['message'] != message:
            self.logs.append(dict(time=time.time(), message=message))
            print(message, flush=True)

    def _new(self, app, options, sid=None):
        options = options_for(app, options)
        sid = sid or uuid.uuid4().hex[:12]
        self.sessions[sid] = dict(app=app, options=options, game=SESSION_FACTORIES[app](options), error=None)
        return sid

    def _assign(self, address, sid):
        old = self.owners.get(address)
        if old == sid:
            return
        if old in self.sessions:
            self.sessions[old]['game'].remove(address)
        self.owners[address] = sid
        self.assigned_at[address] = time.monotonic()
        self.frames[address] = BLACK
        if sid:
            self.sessions[sid]['game'].add(address)
        elif address in self.devices:
            self.backend.output(address, BLACK)

    def _reconcile(self):
        for address in self.devices:
            self._assign(address, self.assignments.get(address))
        used = {self._pending_all, self._legacy_default, *self.assignments.values(), *self.owners.values()}
        for sid in list(self.sessions):
            if sid not in used:
                del self.sessions[sid]

    def _most_used(self, devices):
        counts = Counter(self.assignments[address] for address in devices
                         if self.assignments.get(address) in self.sessions)
        ranked = counts.most_common(2)
        if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            return ranked[0][0]
        return None

    def _command(self, command, values):
        if command == 'state':
            return self._snapshot()
        if command == 'start':
            devices = device_ids(values.get('devices'))
            app = values.get('interaction')
            options = options_for(app, values.get('options'))
            # Joining an interaction preserves its progress and launch settings.
            sid = next((sid for sid, entry in self.sessions.items() if entry['app'] == app), None)
            if sid is None:
                sid = self._new(app, options)
            if devices is None:
                self._pending_all = sid if not self.devices else None
                devices = set(self.devices) | set(self.assignments)
            for address in devices:
                self.assignments[address] = sid
            self._log(f'Started {app} on {"all devices" if values.get("devices") is None else ", ".join(devices)}')
        elif command == 'stop':
            devices = device_ids(values.get('devices'))
            if devices is None:
                self._pending_all = self._legacy_default = None
                devices = set(self.devices) | set(self.assignments)
            for address in devices:
                self.assignments[address] = None
            self._log('Stopped ' + ('all devices' if values.get('devices') is None else ', '.join(devices)))
        elif command == 'restart':
            app = values.get('interaction')
            sid = next((sid for sid, entry in self.sessions.items() if entry['app'] == app), None)
            if sid is None:
                raise ValueError('Interaction is not running')
            entry = self.sessions[sid]
            supplied = values.get('options')
            options_for(app, supplied)
            options = {**entry['options'], **(supplied or {})}
            game = SESSION_FACTORIES[app](options)
            entry.update(game=game, options=options)
            entry['error'] = None
            for address, owner in self.owners.items():
                if owner == sid:
                    entry['game'].add(address)
                    self.assigned_at[address] = time.monotonic()
            self._log(f'Restarted {app} on all assigned devices')
        elif command == 'tap':
            from .devices import DemoBackend
            from controllers.midi import MidiEvent
            if not isinstance(self.backend, DemoBackend):
                raise ValueError('Synthetic input is available only in demo mode')
            address = device_ids([values.get('device')])[0]
            pad = values.get('pad')
            if type(pad) is not int or not 0 <= pad < 16:
                raise ValueError('Invalid pad')
            now = time.monotonic()
            self._event('input', (address, MidiEvent('on', 9, pad + 4, 100, now)))
            self._event('input', (address, MidiEvent('off', 9, pad + 4, 0, now)))
            return self._snapshot()
        else:
            raise ValueError('Unknown command')
        self._reconcile()
        self._save()
        return self._snapshot()

    def _event(self, kind, value):
        if kind == 'devices':
            before = dict(self.assignments)
            # Decide once for the entire discovery batch, before adding votes.
            join = self._legacy_default or (self._most_used(value) if self.auto_join else None)
            for address in value:
                if address not in self.assignments:
                    self.assignments[address] = self._pending_all or join
            if value:
                self._pending_all = self._legacy_default = None
            for address in set(self.devices) - set(value):
                self._assign(address, None)
                self.owners.pop(address, None)
                self.backend.forget(address)
            for address, props in value.items():
                previous = self.devices.get(address, {})
                self.devices[address] = {**previous, **props}
            self.devices = {address: self.devices[address] for address in value}
            self.error = None
            self._reconcile()
            if self.assignments != before:
                self._save()
        elif kind in ('connected', 'disconnected', 'input_reset'):
            address = value[0] if kind == 'disconnected' else value
            if address in self.devices and kind != 'input_reset':
                self.devices[address].update(ready=kind == 'connected', error=None if kind == 'connected' else value[1])
            sid = self.owners.get(address)
            if sid in self.sessions:
                self.sessions[sid]['game'].reconnect(address)
                self.assigned_at[address] = time.monotonic()
            if kind == 'disconnected':
                self._log(f'{address}: {value[1]}')
            elif kind == 'input_reset':
                self._log(f'{address}: Restored Preset 1; cleared held pads')
        elif kind == 'input':
            address, event = value
            sid = self.owners.get(address)
            if sid in self.sessions and event.time >= self.assigned_at.get(address, 0):
                entry = self.sessions[sid]
                if not entry['error']:
                    try:
                        entry['game'].handle(address, event)
                    except Exception as error:
                        entry['error'] = str(error)
                        self._log(f'{entry["app"]}: {error}')
        elif kind == 'error':
            self.error = value
            self._log(value)

    def _tick(self):
        now = time.monotonic()
        for address, sid in self.owners.items():
            if sid not in self.sessions or address not in self.devices:
                continue
            entry = self.sessions[sid]
            try:
                frame = BLACK if entry['error'] else entry['game'].render(address, now)
                self.frames[address] = frame
                self.backend.output(address, frame, entry['options']['fps'], entry['options']['chunk_delay'])
            except Exception as error:
                entry['error'] = str(error)
                self.backend.output(address, BLACK)
                self._log(f'{entry["app"]}: {error}')

    def _loop(self):
        while not self.shutdown.is_set():
            # Bound work per frame so pressure/command floods cannot starve output.
            for _ in range(256):
                try:
                    command, values, future = self.inbox.get(timeout=.01 if _ == 0 else 0)
                except queue.Empty:
                    break
                try:
                    result = self._command(command, values) if future else self._event(command, values)
                    if future:
                        future.set_result(result)
                except Exception as error:
                    if future:
                        future.set_exception(error)
                    else:
                        self.error = str(error)
                        self._log(str(error))
            if self.overflow.is_set():
                self.overflow.clear()
                for address, sid in self.owners.items():
                    if sid in self.sessions:
                        self.sessions[sid]['game'].reconnect(address)
                        self.assigned_at[address] = time.monotonic()
                self._log('Input backlog reset; release and press pads again.')
            self._tick()
            self.shutdown.wait(.015)

    def _snapshot(self):
        return dict(generation=self.generation, error=self.error, logs=list(self.logs),
                    sessions={sid: {k: v for k, v in entry.items() if k != 'game'} for sid, entry in self.sessions.items()},
                    devices=[dict(**props, session=self.owners.get(address),
                                  frame=self.frames.get(address, BLACK)) for address, props in sorted(self.devices.items())])

    def _save(self):
        if not self.state_path:
            return
        data = dict(version=2, assignments=self.assignments,
                    sessions={sid: dict(app=e['app'], options=e['options']) for sid, e in self.sessions.items()})
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data), encoding='utf-8')
        temporary.replace(self.state_path)

    def _restore(self):
        if not self.state_path or not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            if data.get('version') not in (1, 2):
                raise ValueError('Unknown saved state version')
            for sid, entry in data['sessions'].items():
                self._new(entry['app'], entry['options'], sid)
            self._legacy_default = data.get('default') if data['version'] == 1 else None
            self.assignments = data['overrides'] if data['version'] == 1 else data['assignments']
            if self._legacy_default is not None and self._legacy_default not in self.sessions:
                raise ValueError('Unknown default session')
            for address, sid in self.assignments.items():
                device_ids([address])
                if sid is not None and sid not in self.sessions:
                    raise ValueError('Unknown device session')
            # Merge older saves containing separate sessions of the same game.
            # Prefer the automatic-join session's settings, otherwise the first.
            canonical, remap = {}, {}
            order = ([self._legacy_default] if self._legacy_default else []) + list(self.sessions)
            for sid in order:
                app = self.sessions[sid]['app']
                remap[sid] = canonical.setdefault(app, sid)
            self.assignments = {address: remap.get(sid) for address, sid in self.assignments.items()}
            self.sessions = {sid: entry for sid, entry in self.sessions.items() if remap[sid] == sid}
        except (ValueError, KeyError, TypeError, OSError) as error:
            self.sessions, self.assignments, self._legacy_default = {}, {}, None
            self._log(f'Could not restore assignments: {error}')
