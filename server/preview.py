"""Scripted examples rendered by the actual games, with no hardware access."""
from functools import lru_cache
import math
from controllers.midi import MidiEvent
from engine.registry import model, options_for
from interactions.fill import CELEBRATION_SECONDS


@lru_cache(maxsize=8)
def preview(app):
    game = model(app, options_for(app, {'brightness': 1}))
    events = []
    duration = 8
    if app == 'fill':
        start = .5
        for level in range(2):
            for pad in range(16):
                at = start + pad * .28
                events += [MidiEvent('on', 9, 4 + pad, 100, at), MidiEvent('off', 9, 4 + pad, 0, at + .18)]
            # Let the celebration finish before tapping into the next color.
            start = at + CELEBRATION_SECONDS + .5
        duration = start + .25
    elif app == 'flit':
        # Undo and redo a tap, fill white, then try blue and return to white.
        # Returning to white reverses the sequence, so the final round is black.
        rounds = ((0, 1, 0, 0, *range(2, 16)), (0, 1, 0, 1), tuple(range(16)))
        start = .5
        for pads in rounds:
            for step, pad in enumerate(pads):
                at = start + step * .28
                events += [MidiEvent('on', 9, 4 + pad, 100, at), MidiEvent('off', 9, 4 + pad, 0, at + .18)]
            start = at + CELEBRATION_SECONDS + .5
        duration = start + .25
    elif app == 'coloring':
        # A tiny multicolored heart, built one tap at a time.
        for n, pad in enumerate((13, 14, 8, 9, 10, 11, 4, 5, 6, 7, 1, 2, 9, 10, 5, 6)):
            at = .4 + n * .25
            events += [MidiEvent('on', 9, 4 + pad, 100, at), MidiEvent('off', 9, 4 + pad, 0, at + .18)]
    else:
        events = [MidiEvent('on', 9, 4, 100, .2), MidiEvent('on', 9, 14, 100, 1.7),
                  MidiEvent('off', 9, 4, 0, 4), MidiEvent('off', 9, 14, 0, 4.8)]
    events.sort(key=lambda event: event.time)
    frames, pressed = [], []
    held = set()
    for tick in range(math.ceil(duration * 12)):
        now = tick / 12
        while events and events[0].time <= now:
            event = events.pop(0)
            game.handle(event)
            pad = event.note - 4
            if event.kind == 'on':
                held.add(pad)
            else:
                held.discard(pad)
        frames.append(game.render(now))
        pressed.append(sorted(held))
    return dict(fps=12, frames=frames, pressed=pressed)
