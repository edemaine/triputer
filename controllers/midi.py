"""MIDI events and ALSA input diagnostics."""

import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from .jpmini import note_to_position


@dataclass(frozen=True)
class MidiEvent:
    kind: str
    channel: int
    note: int
    value: int
    time: float


def parse_event(line, timestamp):
    match = re.match(
        r"^\s*\d+:\d+\s+(Note on|Note off|Polyphonic aftertouch)\s+"
        r"(\d+), note (\d+), (?:velocity|value) (\d+)\s*$", line,
    )
    if match is None:
        return None
    kind, channel, note, value = match.groups()
    kind = {"Note on": "on", "Note off": "off", "Polyphonic aftertouch": "pressure"}[kind]
    if kind == "on" and int(value) == 0:
        kind = "off"
    return MidiEvent(kind, int(channel), int(note), int(value), timestamp)


class MidiMonitor:
    def __init__(self, port, base_note=4, verbose=True):
        listing = subprocess.check_output(["aseqdump", "-l"], text=True)
        if not port:
            ports = re.findall(r"^\s*(\d+:\d+)\s+JP-Mini\b.*$", listing, re.MULTILINE)
            if len(ports) != 1:
                raise RuntimeError(
                    "Select a MIDI port using --midi-port CLIENT:PORT. Available ports:\n" + listing
                )
            port = ports[0]
        print(f"MIDI input: {port}. Press, hold, and release pads.", flush=True)
        self.events = 0
        self.base_note = base_note
        self.verbose = verbose
        self.pending = queue.SimpleQueue()
        self.process = subprocess.Popen(
            ["stdbuf", "-oL", "aseqdump", "-p", port],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            env={**os.environ, "LC_ALL": "C"},
        )
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        for line in self.process.stdout:
            if re.match(r"^\s*\d+:\d+\s", line):
                self.events += 1
            event = parse_event(line, time.monotonic())
            # Diagnostic mode only prints; animation mode queues events for the
            # render thread so it never races the MIDI reader's state updates.
            if event is not None and not self.verbose:
                self.pending.put(event)
            if not self.verbose:
                if event is None and not line.startswith(("Waiting for", "Source")):
                    print("MIDI | " + line.rstrip(), flush=True)
                continue
            description = line.rstrip()
            note = re.search(r"\bnote (\d+)\b", line)
            if note:
                position = note_to_position(int(note[1]), self.base_note)
                if position is not None:
                    row, column = position
                    description += f" | row {row}, column {column}"
                else:
                    description += " | outside configured pad range"
            print("MIDI | " + description, flush=True)

    def drain(self):
        while True:
            try:
                yield self.pending.get_nowait()
            except queue.Empty:
                return

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.thread.join(timeout=2)
        self.process.stdout.close()

