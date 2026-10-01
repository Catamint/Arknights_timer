"""An append-only causal event journal with local identities."""
from threading import RLock

from ._data import clone, integer, name, readonly


class EventLog:
    def __init__(self, lock=None):
        self._lock = lock or RLock()
        self._records = []
        self._next_id = 1

    def emit(self, event_type, payload, time, cause=None):
        with self._lock:
            name(event_type, "event type")
            integer(time, "event time", 0)
            if cause is not None:
                integer(cause, "cause event ID", 1)
                if cause >= self._next_id:
                    raise ValueError(f"Cause event does not exist: {cause}")
            payload = clone(payload)
            event_id = self._next_id
            self._next_id += 1
            self._records.append(readonly({"id": event_id, "type": event_type, "payload": payload,
                                          "time": time, "cause": cause}))
            return event_id

    @property
    def records(self):
        with self._lock:
            return tuple(self._records)

    def iter_records(self, start=0):
        """Read a stable immutable slice without cloning historical payloads."""
        with self._lock:
            integer(start, "event journal start", 0)
            return iter(tuple(self._records[start:]))

    def snapshot(self):
        with self._lock:
            return clone({"records": self._records, "next_id": self._next_id})

    def restore(self, data):
        with self._lock:
            data = clone(data)
            previous = 0
            previous_time = 0
            for record in data["records"]:
                if not isinstance(record, dict) or not {"id", "type", "payload", "time", "cause"}.issubset(record):
                    raise ValueError("Event checkpoint lacks required fields")
                event_id = integer(record["id"], "event ID", 1)
                if event_id != previous + 1:
                    raise ValueError("Event IDs must be contiguous and ordered")
                name(record["type"], "event type")
                integer(record["time"], "event time", 0)
                if record["time"] < previous_time:
                    raise ValueError("Event times must be nondecreasing")
                cause = record["cause"]
                if cause is not None:
                    integer(cause, "cause event ID", 1)
                    if cause >= event_id:
                        raise ValueError("Cause must refer to an earlier event")
                previous = event_id
                previous_time = record["time"]
            next_id = integer(data["next_id"], "next event ID", 1)
            if next_id != previous + 1:
                raise ValueError("Invalid next event ID")
            self._records = [readonly(record) for record in data["records"]]
            self._next_id = next_id
