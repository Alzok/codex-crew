from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional


EventHandler = Callable[[Dict[str, object]], None]


@dataclass
class EventStats:
    event: str
    count: int
    last_emitted: Optional[float]


class EventBus:
    """Simple in-process event bus with metrics tracking."""

    def __init__(self, *, debug: bool = False) -> None:
        self._listeners: Dict[str, List[EventHandler]] = defaultdict(list)
        self._lock = threading.RLock()
        self._counts: Dict[str, int] = defaultdict(int)
        self._last_emitted: Dict[str, float] = {}
        self._debug = debug

    def emit(self, event: str, payload: Optional[Dict[str, object]] = None) -> None:
        if payload is None:
            payload = {}
        with self._lock:
            listeners = list(self._listeners.get(event, []))
            self._counts[event] += 1
            self._last_emitted[event] = time.time()
        if self._debug:
            print(f"[EventBus] {event}: {payload}")
        for handler in listeners:
            try:
                handler(payload)
            except Exception:  # noqa: BLE001
                # Silently ignore handler errors to avoid breaking emitters
                continue

    def subscribe(self, event: str, handler: EventHandler) -> Callable[[], None]:
        with self._lock:
            self._listeners[event].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._listeners.get(event)
                if handlers and handler in handlers:
                    handlers.remove(handler)
        return unsubscribe

    def once(self, event: str, handler: EventHandler) -> Callable[[], None]:
        def wrapper(payload: Dict[str, object]) -> None:
            unsubscribe()
            handler(payload)

        unsubscribe = self.subscribe(event, wrapper)
        return unsubscribe

    def wait_for(self, event: str, timeout: Optional[float] = None) -> Dict[str, object]:
        condition = threading.Event()
        result: Dict[str, object] = {}

        def _handler(payload: Dict[str, object]) -> None:
            result.update(payload)
            condition.set()

        unsubscribe = self.subscribe(event, _handler)
        triggered = condition.wait(timeout)
        unsubscribe()
        if not triggered:
            raise TimeoutError(f"Timeout waiting for event: {event}")
        return result

    def get_stats(self) -> List[EventStats]:
        with self._lock:
            return [
                EventStats(event=event, count=count, last_emitted=self._last_emitted.get(event))
                for event, count in self._counts.items()
            ]

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._last_emitted.clear()


# Singleton instance used across Numerus
EVENT_BUS = EventBus()

