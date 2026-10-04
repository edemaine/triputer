"""Bluetooth I/O workers. They never execute interaction code."""
import threading
import time
from controllers.bluetooth import BluetoothSession, BusPool, DEVICE, managed_objects, paired_controllers, start_dbus_loop
from controllers.midi import MidiEvent

BLACK = [(0, 0, 0)] * 16


class DeviceWorker(threading.Thread):
    def __init__(self, address, emit, factory):
        super().__init__(name=f'bluetooth-{address}', daemon=True)
        self.address, self.emit, self.factory = address, emit, factory
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame, self.fps, self.delay = BLACK, 8., .03

    def output(self, frame, fps, delay):
        with self.lock:
            self.frame, self.fps, self.delay = frame, fps, delay

    def run(self):
        failures = 0
        while not self.stop_event.is_set():
            session = None
            try:
                session = self.factory(self.address, self.delay)
                session.open(self.stop_event)
                self.emit('connected', self.address)
                started = time.monotonic()
                while not self.stop_event.is_set():
                    tick = time.monotonic()
                    events = session.drain()
                    if getattr(session, 'input_reset', False):
                        session.input_reset = False
                        self.emit('input_reset', self.address)
                    for event in events:
                        self.emit('input', (self.address, event))
                    with self.lock:
                        frame, fps, delay = self.frame, self.fps, self.delay
                    session.lights.chunk_delay = delay
                    session.send(frame)
                    if time.monotonic() - started >= 5:
                        failures = 0
                    self.stop_event.wait(max(0, 1 / fps - (time.monotonic() - tick)))
            except Exception as error:
                failures += 1
                wait = min(15, 2 ** min(failures, 4))
                if not self.stop_event.is_set():
                    self.emit('disconnected', (self.address, f'{error}; retrying in {wait}s'))
            finally:
                if session:
                    try:
                        session.close(clear=self.stop_event.is_set())
                    except Exception:
                        pass
            if not self.stop_event.is_set():
                self.stop_event.wait(wait)


class BluetoothBackend:
    def __init__(self):
        self.buses = BusPool()
        self.workers = {}
        self.stop = threading.Event()
        self.loop = self.loop_thread = self.discovery = None

    def start(self, emit):
        self.emit = emit
        self.loop, self.loop_thread = start_dbus_loop()
        self.discovery = threading.Thread(target=self.discover, name='device-discovery', daemon=True)
        self.discovery.start()

    def discover(self):
        while not self.stop.is_set():
            try:
                objects = managed_objects(self.buses.get('discovery'))
                paired = paired_controllers(objects)
                devices = {}
                for interfaces in objects.values():
                    props = interfaces.get(DEVICE, {})
                    address = str(props.get('Address', '')).upper()
                    if address in paired:
                        devices[address] = dict(address=address, name=str(props.get('Alias', 'JP MINI')),
                                                connected=bool(props.get('Connected')))
                self.emit('devices', devices)
            except Exception as error:
                self.emit('error', f'Bluetooth discovery: {error}')
            self.stop.wait(2)

    def output(self, address, frame, fps=8, delay=.03):
        worker = self.workers.get(address)
        if worker is None:
            worker = DeviceWorker(address, self.emit, lambda a, d: BluetoothSession(a, d, self.buses.get(a)))
            self.workers[address] = worker
            worker.output(frame, fps, delay)
            worker.start()
        else:
            worker.output(frame, fps, delay)

    def forget(self, address):
        worker = self.workers.pop(address, None)
        if worker:
            worker.stop_event.set()
            worker.join()

    def close(self):
        self.stop.set()
        if self.discovery:
            self.discovery.join()
        for worker in self.workers.values():
            worker.stop_event.set()
        for worker in self.workers.values():
            worker.join()
        if self.loop:
            self.loop.quit()
            self.loop_thread.join()
        self.buses.close()


class DemoBackend:
    """Hardware-free devices for UI development and tests."""
    def __init__(self, devices=None):
        self.devices = devices if devices is not None else {
            f'00:00:00:00:00:0{i}': dict(address=f'00:00:00:00:00:0{i}', name=name, connected=True)
            for i, name in enumerate(('Clover', 'Peach', 'Sky'), 1)
        }
        self.frames = {}

    def start(self, emit):
        self.emit = emit
        emit('devices', self.devices)

    def output(self, address, frame, fps=8, delay=.03):
        self.frames[address] = frame

    def forget(self, address):
        self.frames.pop(address, None)

    def close(self):
        pass
