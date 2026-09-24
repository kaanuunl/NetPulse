"""In-memory event feed shown in the dashboard and forwarded to notifications."""

from __future__ import annotations

import contextlib
import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

Listener = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    id: int
    ts: float
    kind: str
    level: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "kind": self.kind, "level": self.level, "data": self.data}


class EventLog:
    def __init__(self, capacity: int = 300, clock: Callable[[], float] = time.time):
        self._events: deque[Event] = deque(maxlen=capacity)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._clock = clock

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def add(self, kind: str, level: str = "info", **data: Any) -> Event:
        with self._lock:
            event = Event(next(self._ids), self._clock(), kind, level, data)
            self._events.append(event)
        for listener in list(self._listeners):
            # A broken notifier must never stop collection.
            with contextlib.suppress(Exception):
                listener(event)
        return event

    def since(self, event_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            newer = [e for e in self._events if e.id > event_id]
        return [e.to_dict() for e in newer[-limit:]]
