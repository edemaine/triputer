"""Interaction catalog and session factories; safe to import without Bluetooth."""
import importlib
import math

CATALOG = {
    'fill': dict(name='Fill', symbol='▦', color='#ffc857', description='Fill every square to unlock the next color.'),
    'flit': dict(name='Flit', symbol='⇄', color='#b6a2ff', description='Tap to toggle between two colors. Match the grid to continue or reverse the color sequence.'),
    'coloring': dict(name='Coloring', symbol='✿', color='#ff86ae', description='Tap a square to cycle its color and draw your own 4×4 picture.'),
    'ripple': dict(name='Ripples', symbol='◎', color='#79d8cf', description='Hold a pad to send rings of color across the grid. Try several fingers and watch their colors meet.'),
}
CLASSES = {'fill': 'Fill', 'flit': 'Flit', 'coloring': 'Coloring', 'ripple': 'Ripples'}
DEFAULTS = dict(brightness=.25, fps=8., chunk_delay=.03, base_note=None)
LIMITS = dict(brightness=(.001, 1), fps=(1, 60), chunk_delay=(.005, 1), speed=(.1, 20), period=(.1, 30), fade=(.1, 60))


def options_for(app, supplied=None):
    if app not in CATALOG:
        raise ValueError('Unknown interaction')
    result = dict(DEFAULTS)
    if app == 'ripple':
        result.update(speed=2.5, period=1.2, fade=2.)
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError('Options must be an object')
    for key, value in (supplied or {}).items():
        if key not in result:
            raise ValueError(f'Unknown option: {key}')
        if key == 'base_note':
            if value is not None and (type(value) is not int or not 0 <= value <= 112):
                raise ValueError('base_note must be an integer from 0 to 112')
        elif isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not LIMITS[key][0] <= value <= LIMITS[key][1]:
            raise ValueError(f'Invalid {key}')
        result[key] = value
    return result


def model(app, options):
    cls = getattr(importlib.import_module(f'interactions.{app}'), CLASSES[app])
    keys = ('base_note', 'brightness', 'speed', 'period', 'fade') if app == 'ripple' else ('base_note', 'brightness')
    return cls(**{key: options[key] for key in keys})


class IndependentSession:
    """Adapter for today's games. Future sessions can share state across devices.

    The engine calls add/remove/reconnect/handle/render on one thread. A session
    can react to an event from one device by changing render output for any other.
    """
    def __init__(self, app, options):
        self.app, self.options = app, options
        self.models = {}

    def add(self, device):
        self.models[device] = model(self.app, self.options)

    def remove(self, device):
        self.models.pop(device, None)

    def reconnect(self, device):
        self.models[device].reconnect()

    def handle(self, device, event):
        return self.models[device].handle(event)

    def render(self, device, now):
        return self.models[device].render(now)


# Register a custom factory here for a future interaction spanning controllers.
SESSION_FACTORIES = {app: (lambda options, app=app: IndependentSession(app, options)) for app in CATALOG}
