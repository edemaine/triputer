"""Real HTTP API, argument validation, and hardware-free previews."""
import json
from http.client import HTTPConnection
import math
from pathlib import Path
import subprocess
import sys
import socket
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from engine import Engine
from engine.devices import DemoBackend
from server.app import make_server
from server.preview import preview


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(DemoBackend()).start()
        self.server = make_server(self.engine, '127.0.0.1', 0, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.engine.close()

    def request(self, path, values=None, headers=None):
        body = None if values is None else json.dumps(values).encode()
        request = Request(self.url + path, data=body, headers=headers or {'Content-Type':'application/json'})
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def check_listener(self, host, addresses, family):
        server = make_server(self.engine, host, 0, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(server.address_family, family)
            for address in addresses:
                with self.subTest(address=address):
                    connection = HTTPConnection(address, server.server_port, timeout=3)
                    try:
                        connection.request('GET', '/api/state')
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertIn('fill', json.loads(response.read())['catalog'])
                    finally:
                        connection.close()
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    @unittest.skipUnless(socket.has_dualstack_ipv6(), 'Dual-stack IPv6 unavailable')
    def test_default_listener_accepts_ipv4_and_ipv6(self):
        self.check_listener(None, ('127.0.0.1', '::1'), socket.AF_INET6)

    @unittest.skipUnless(socket.has_ipv6, 'IPv6 unavailable')
    def test_explicit_ipv6_listener(self):
        self.check_listener('::1', ('::1',), socket.AF_INET6)

    def test_default_listener_falls_back_without_dualstack(self):
        with patch('server.app.socket.has_dualstack_ipv6', return_value=False):
            self.check_listener(None, ('127.0.0.1',), socket.AF_INET)

    def test_start_stop_state_and_preview(self):
        self.assertEqual(len(self.request('/api/state')['devices']), 3)
        started = self.request('/api/start', {'interaction':'fill'})
        self.assertTrue(all(d['session'] for d in started['devices']))
        self.assertNotIn('default', started)
        preview_data = self.request('/api/preview/fill')
        self.assertEqual(len(preview_data['frames']), len(preview_data['pressed']))
        self.assertTrue(all(d['session'] is None for d in self.request('/api/stop', {})['devices']))
        with urlopen(self.url) as response:
            self.assertIn(b'Start on all', response.read())

    def test_rejects_cross_origin_and_invalid_commands(self):
        cases = [('/api/start', {'interaction':'fill'}, {'Content-Type':'application/json','Origin':'https://elsewhere.invalid'}, 403),
                 ('/api/start', {'interaction':'unknown'}, None, 400),
                 ('/api/start', {'interaction':'fill','devices':['bad']}, None, 400),
                 ('/api/anything', {}, None, 404)]
        for path, values, headers, status in cases:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, values, headers)
            self.assertEqual(error.exception.code, status)

    def test_flit_launch_and_controller_isolation(self):
        state = self.request('/api/state')
        self.assertEqual(state['catalog']['flit']['name'], 'Flit')
        a, b = (device['address'] for device in state['devices'][:2])
        self.request('/api/start', {'interaction':'flit', 'devices':[a, b]})
        for expected in ([64, 64, 64], None, [0, 64, 64]):
            self.request('/api/tap', {'device':a, 'pad':0})
            state = self.request('/api/state')
            if expected is not None:  # The second tap starts the black celebration.
                self.assertEqual(state['devices'][0]['frame'][0], expected)
            self.assertEqual(state['devices'][1]['frame'], [[0, 0, 0]] * 16)
        data = self.request('/api/preview/flit')
        self.assertEqual(data['frames'][-1], [[0, 0, 0]] * 16)

    def test_assignments_join_one_interaction_and_restart_is_explicit(self):
        devices = self.request('/api/state')['devices']
        a, b = (d['address'] for d in devices[:2])
        self.request('/api/start', {'interaction':'coloring', 'devices':[a]})
        self.request('/api/tap', {'device':a, 'pad':0})
        self.request('/api/start', {'interaction':'coloring', 'devices':[b]})
        state = self.request('/api/state')
        self.assertEqual(len(state['sessions']), 1)
        self.assertEqual(state['devices'][0]['session'], state['devices'][1]['session'])
        self.assertEqual(state['devices'][0]['frame'][0], [64, 0, 0])
        self.request('/api/restart', {'interaction':'coloring'})
        self.assertEqual(self.request('/api/state')['devices'][0]['frame'][0], [0, 0, 0])

    def test_preview_uses_real_animation_and_has_moving_frames(self):
        for app in ('fill','flit','coloring','ripple'):
            frames = preview(app)['frames']
            self.assertTrue(all(len(frame)==16 for frame in frames))
            self.assertGreater(len({str(frame) for frame in frames}), 3)

    def test_preview_press_indicators_and_two_fill_rounds(self):
        for app in ('fill', 'flit', 'coloring', 'ripple'):
            data = preview(app)
            self.assertEqual(len(data['frames']), len(data['pressed']))
            self.assertTrue(any(data['pressed']))
            self.assertEqual(data['pressed'][-1], [])
            self.assertTrue(all(0 <= pad < 16 for pads in data['pressed'] for pad in pads))
        ripple = preview('ripple')['pressed']
        self.assertIn([0, 10], ripple)
        data = preview('fill')
        self.assertEqual(set(p for pads in data['pressed'] for p in pads), set(range(16)))
        self.assertEqual(data['frames'][-1], [(0, 0, 255)] * 16)
        self.assertTrue(any((255, 255, 255) in frame and (0, 0, 255) in frame for frame in data['frames']))
        self.assertEqual(data['pressed'][math.ceil(.5 * data['fps'])], [0])

    def test_cli_direct_and_module_device_option_and_no_address_alias(self):
        root = Path(__file__).resolve().parent.parent
        for app in ('fill','flit','coloring','ripple'):
            for prefix in ([str(root/'interactions'/f'{app}.py')], ['-m', f'interactions.{app}']):
                result = subprocess.run([sys.executable, *prefix, '--help'], cwd=root, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b'--device', result.stdout)
                self.assertNotIn(b'--address', result.stdout)
            result = subprocess.run([sys.executable, '-m', f'interactions.{app}', '--address','AA:BB:CC:DD:EE:FF'], cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 2)

    def test_flit_preview_shows_toggles_and_reverses_from_blue_to_black(self):
        data = preview('flit')
        frames = data['frames']
        black, white, blue = (0, 0, 0), (255, 255, 255), (0, 0, 255)
        # Two pads are white; undoing the first leaves the other white.
        undone = frames[math.ceil(1.06 * data['fps'])]
        self.assertEqual(undone[:2], [black, white])
        self.assertEqual(frames[math.ceil(1.34 * data['fps'])][:2], [white, white])
        self.assertIn([white] * 16, frames)
        blue_mix = next(index for index, frame in enumerate(frames) if white in frame and blue in frame)
        self.assertTrue(any(white in frame and black in frame for frame in frames[blue_mix + 1:]))
        self.assertEqual(frames[-1], [black] * 16)


if __name__ == '__main__':
    unittest.main()
