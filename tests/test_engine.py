"""Session routing, persistence, isolation, and the shared hardware lock."""
from pathlib import Path
import tempfile
import json
import time
import unittest
from unittest.mock import patch

from controllers.midi import MidiEvent
from engine import Engine
from engine.devices import DemoBackend
from engine.lock import EngineLock
from engine.registry import SESSION_FACTORIES, options_for

A, B, C = ('00:00:00:00:00:01', '00:00:00:00:00:02', '00:00:00:00:00:03')


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.backend = DemoBackend()
        self.engine = Engine(self.backend).start()
        self.engine.call('state')

    def tearDown(self):
        self.engine.close()

    def owners(self):
        return {d['address']: d['session'] for d in self.engine.call('state')['devices']}

    def test_preset_correction_preserves_progress_and_clears_held_pads(self):
        self.engine.call('start', interaction='coloring')
        self.backend.emit('input', (A, MidiEvent('on', 9, 4, 100, time.monotonic())))
        self.engine.call('state')  # Wait for the event's render tick.
        before = self.engine.call('state')
        self.backend.emit('input_reset', A)
        after = self.engine.call('state')
        self.assertEqual(before['devices'][0]['frame'], after['devices'][0]['frame'])
        # No release was received, but correction clears the held-note latch.
        self.backend.emit('input', (A, MidiEvent('on', 9, 4, 100, time.monotonic())))
        self.engine.call('state')
        advanced = self.engine.call('state')
        self.assertNotEqual(after['devices'][0]['frame'], advanced['devices'][0]['frame'])
        self.assertIn('Restored Preset 1', after['logs'][-1]['message'])

    def tap(self, address=A):
        self.engine.call('tap', device=address, pad=0)
        # Inspect through an engine-thread command, after the render tick.
        return self.engine.call('state')

    def test_start_all_then_override_keeps_other_devices_game_state(self):
        self.engine.call('start', interaction='coloring')
        before = self.owners()
        self.assertEqual(len(set(before.values())), 1)
        self.tap(B)
        self.engine.call('start', interaction='fill', devices=[A])
        after = self.owners()
        self.assertNotEqual(after[A], before[A])
        self.assertEqual(after[B], before[B])
        self.assertEqual(after[C], before[C])
        state = self.engine.call('state')
        self.assertEqual(state['devices'][1]['frame'][0], (64, 0, 0))
        self.engine.call('start', interaction='coloring', devices=[A])
        self.assertEqual(self.owners()[A], before[A])

    def test_new_pairing_joins_most_used_interaction(self):
        self.engine.call('start', interaction='fill')
        self.engine.call('start', interaction='coloring', devices=[A, B])
        owners = self.owners()
        self.assertEqual(owners[A], owners[B])
        self.assertNotEqual(owners[A], owners[C])
        extra = '00:00:00:00:00:04'
        self.backend.emit('devices', {**self.backend.devices, extra: dict(address=extra, name='New', connected=True)})
        self.assertEqual(self.owners()[extra], owners[A])

    def test_joining_existing_interaction_preserves_progress_and_settings(self):
        self.engine.call('start', interaction='coloring', devices=[A], options={'brightness': .4})
        self.tap(A)
        sid = self.owners()[A]
        self.engine.call('start', interaction='coloring', devices=[B], options={'brightness': .8})
        state = self.engine.call('state')
        self.assertEqual(self.owners()[A], self.owners()[B])
        self.assertEqual(len(state['sessions']), 1)
        self.assertEqual(state['sessions'][sid]['options']['brightness'], .4)
        self.assertEqual(state['devices'][0]['frame'][0], (102, 0, 0))
        self.assertEqual(state['devices'][1]['frame'][0], (0, 0, 0))
        self.engine.call('start', interaction='coloring', devices=[A])
        self.assertEqual(self.engine.call('state')['devices'][0]['frame'][0], (102, 0, 0))
        self.engine.call('start', interaction='coloring')
        self.assertEqual(set(self.owners().values()), {sid})
        self.assertNotIn('default', self.engine.call('state'))
        self.assertEqual(self.engine.call('state')['devices'][0]['frame'][0], (102, 0, 0))

    def test_restart_resets_every_controller_in_interaction_only(self):
        self.engine.call('start', interaction='coloring', devices=[A, B])
        self.engine.call('start', interaction='fill', devices=[C])
        for address in (A, B, C):
            self.tap(address)
        owners = self.owners()
        self.engine.call('restart', interaction='coloring', options={'brightness': .5})
        state = self.engine.call('state')
        self.assertEqual(self.owners(), owners)
        self.assertEqual(state['devices'][0]['frame'][0], (0, 0, 0))
        self.assertEqual(state['devices'][1]['frame'][0], (0, 0, 0))
        self.assertEqual(state['devices'][2]['frame'][0], (64, 64, 64))
        self.tap(B)
        self.assertEqual(self.engine.call('state')['devices'][1]['frame'][0], (128, 0, 0))
        with self.assertRaises(ValueError):
            self.engine.call('restart', interaction='coloring', options={'brightness': 9})
        self.assertEqual(self.engine.call('state')['devices'][1]['frame'][0], (128, 0, 0))
        with self.assertRaisesRegex(ValueError, 'not running'):
            self.engine.call('restart', interaction='ripple')

    def test_stopped_controller_rejoins_existing_interaction(self):
        self.engine.call('start', interaction='coloring')
        sid = self.owners()[B]
        self.tap(B)
        self.engine.call('stop', devices=[A])
        self.engine.call('start', interaction='coloring', devices=[A])
        self.assertEqual(self.owners()[A], sid)
        self.assertEqual(self.engine.call('state')['devices'][1]['frame'][0], (64, 0, 0))

    def test_individual_stop_then_start_all_replaces_overrides(self):
        self.engine.call('start', interaction='fill')
        self.engine.call('stop', devices=[A])
        self.assertIsNone(self.owners()[A])
        self.assertIsNotNone(self.owners()[B])
        self.engine.call('start', interaction='ripple')
        self.assertEqual(len(set(self.owners().values())), 1)
        self.engine.call('stop')
        self.assertTrue(all(sid is None for sid in self.owners().values()))
        self.assertEqual(self.engine.call('state')['sessions'], {})

    def test_queued_input_before_assignment_is_not_delivered_to_new_game(self):
        old = MidiEvent('on', 9, 4, 100, time.monotonic() - 1)
        self.engine.call('start', interaction='coloring')
        self.backend.emit('input', (A, old))
        self.assertEqual(self.engine.call('state')['devices'][0]['frame'], [(0, 0, 0)] * 16)
        self.tap()
        self.assertEqual(self.engine.call('state')['devices'][0]['frame'][0], (64, 0, 0))

    def test_reconnect_preserves_coloring_and_resets_ripple_sources(self):
        self.engine.call('start', interaction='coloring')
        self.tap()
        self.backend.emit('connected', A)
        self.assertEqual(self.engine.call('state')['devices'][0]['frame'][0], (64, 0, 0))
        self.engine.call('start', interaction='ripple', devices=[A])
        self.backend.emit('input', (A, MidiEvent('on', 9, 4, 100, time.monotonic())))
        self.engine.call('state')
        self.backend.emit('connected', A)
        self.engine.call('state')
        frame = self.engine.call('state')['devices'][0]['frame']
        self.assertTrue(all(max(rgb) <= 3 for rgb in frame))

    def test_session_interface_can_share_state_across_devices(self):
        class Shared:
            def __init__(self, options): self.count = 0
            def add(self, device): pass
            def remove(self, device): pass
            def reconnect(self, device): pass
            def handle(self, device, event):
                if event.kind == 'on': self.count += 1
            def render(self, device, now): return [(self.count, 0, 0)] * 16
        with patch.dict(SESSION_FACTORIES, fill=Shared):
            self.engine.call('start', interaction='fill', devices=[A, B])
            self.tap(A)
            state = self.engine.call('state')
            self.assertEqual(state['devices'][0]['frame'], state['devices'][1]['frame'])
            self.assertEqual(state['devices'][1]['frame'][0], (1, 0, 0))

    def test_bad_options_cannot_stop_current_session(self):
        self.engine.call('start', interaction='fill')
        before = self.owners()
        for options in ({'brightness': float('nan')}, {'brightness': True}, {'command':'bad'}, {'fps':0}):
            with self.assertRaises(ValueError):
                self.engine.call('start', interaction='fill', options=options)
        with self.assertRaises(ValueError):
            self.engine.call('start', interaction='fill', devices=[])
        self.assertEqual(self.owners(), before)

    def test_state_restores_groups_overrides_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            first = Engine(DemoBackend(), state_path=path).start()
            try:
                first.call('start', interaction='fill', options={'brightness':.4})
                first.call('start', interaction='coloring', devices=[A, B])
                first.call('stop', devices=[C])
                expected = first.call('state')
            finally:
                first.close()
            second = Engine(DemoBackend(), state_path=path).start()
            try:
                restored = second.call('state')
                self.assertEqual(restored['sessions'], expected['sessions'])
                self.assertEqual([d['session'] for d in restored['devices']], [d['session'] for d in expected['devices']])
            finally:
                second.close()

    def test_hardware_lock_excludes_second_engine_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            one, two = EngineLock(Path(folder)/'lock'), EngineLock(Path(folder)/'lock')
            try:
                one.acquire()
                with self.assertRaisesRegex(RuntimeError, 'Another Triputer engine'):
                    two.acquire()
                one.close()
                two.acquire()
            finally:
                one.close()
                two.close()

    def test_restore_merges_legacy_duplicate_interactions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text(json.dumps(dict(version=1, default='main', overrides={A:'other', B:'fill2', C:None},
                sessions={'other':dict(app='coloring', options={'brightness': .8}),
                          'main':dict(app='coloring', options={'brightness': .4}),
                          'fill1':dict(app='fill', options={}), 'fill2':dict(app='fill', options={})})))
            restored = Engine(DemoBackend(), state_path=path).start()
            try:
                state = restored.call('state')
                self.assertEqual(set(state['sessions']), {'main', 'fill1'})
                self.assertEqual([d['session'] for d in state['devices']], ['main', 'fill1', None])
                self.assertEqual(state['sessions']['main']['options']['brightness'], .4)
                restored.call('start', interaction='fill', devices=[C])
                saved = json.loads(path.read_text())
                self.assertEqual(set(saved['sessions']), {'main', 'fill1'})
                self.assertEqual(saved['assignments'][C], 'fill1')
                self.assertNotIn('default', saved)
            finally:
                restored.close()

    def test_tied_games_leave_new_devices_idle_and_stopped_devices_stay_stopped(self):
        self.engine.call('start', interaction='fill', devices=[A])
        self.engine.call('start', interaction='coloring', devices=[B])
        self.engine.call('stop', devices=[C])
        extras = {f'00:00:00:00:00:0{i}': dict(address=f'00:00:00:00:00:0{i}', name='New', connected=True) for i in (4, 5)}
        self.backend.emit('devices', {**self.backend.devices, **extras})
        self.assertTrue(all(self.owners()[a] is None for a in extras))
        self.engine.call('start', interaction='fill', devices=[B])
        self.backend.emit('devices', {**self.backend.devices, **extras})
        self.assertIsNone(self.owners()[C])
        self.assertTrue(all(self.owners()[a] is None for a in extras))

    def test_majority_is_recomputed_after_assignments_change(self):
        self.engine.call('start', interaction='fill')
        self.engine.call('start', interaction='coloring', devices=[A, B])
        self.engine.call('start', interaction='ripple', devices=[B, C])
        extra = '00:00:00:00:00:04'
        self.backend.emit('devices', {**self.backend.devices, extra:dict(address=extra, name='New', connected=True)})
        self.assertEqual(self.owners()[extra], self.owners()[B])
        self.assertNotEqual(self.owners()[extra], self.owners()[A])

    def test_start_before_discovery_and_cli_device_restriction(self):
        for auto_join in (False, True):
            with self.subTest(auto_join=auto_join):
                backend = DemoBackend({})
                engine = Engine(backend, auto_join=auto_join).start()
                try:
                    engine.call('state')
                    engine.call('start', interaction='fill', devices=[A] if not auto_join else None)
                    backend.emit('devices', {A:self.backend.devices[A]})
                    self.assertIsNotNone(engine.call('state')['devices'][0]['session'])
                    backend.emit('devices', self.backend.devices)
                    state = engine.call('state')
                    self.assertEqual(state['devices'][1]['session'] is not None, auto_join)
                finally:
                    engine.close()

    def test_legacy_automatic_assignment_migrates_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            path.write_text(json.dumps(dict(version=1, default='old', overrides={C:None},
                sessions={'old':dict(app='fill', options={})})))
            engine = Engine(DemoBackend(), state_path=path).start()
            try:
                state = engine.call('state')
                self.assertEqual([d['session'] for d in state['devices']], ['old', 'old', None])
                saved = json.loads(path.read_text())
                self.assertEqual(saved['version'], 2)
                self.assertEqual(saved['assignments'], {A:'old', B:'old', C:None})
                self.assertNotIn('default', saved)
                engine.call('start', interaction='coloring', devices=[A, C])
                extra = '00:00:00:00:00:04'
                engine.backend.emit('devices', {**engine.backend.devices, extra:dict(address=extra, name='New', connected=True)})
                state = engine.call('state')
                self.assertEqual(state['devices'][3]['session'], state['devices'][0]['session'])
            finally:
                engine.close()


if __name__ == '__main__':
    unittest.main()
