"""Battle-local event identities and causal records."""
from copy import deepcopy
from .clock import RATE


class EventLog:
    def __init__(self):
        self.log = []

    def emit(self, tick, event_type, data=None):
        event = {"seq": len(self.log) + 1, "tick": tick, "t": tick / RATE,
                 "type": event_type, "data": deepcopy(data or {})}
        self.log.append(event)
        return event

    def snapshot_events(self, since_seq=0):
        return deepcopy([e for e in self.log if e["seq"] > since_seq])
