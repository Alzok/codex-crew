from __future__ import annotations

import time
from typing import Callable, Iterable, Optional, Type, TypeVar

T = TypeVar("T")


class CircuitBreakerOpen(RuntimeError):
    """Raised when a circuit breaker is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        threshold: int = 3,
        cooldown: float = 30.0,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self._failure_count = 0
        self._opened_until: float = 0.0

    def allow(self) -> None:
        if time.time() < self._opened_until:
            raise CircuitBreakerOpen(f"Circuit '{self.name}' open until {self._opened_until}")

    def record_success(self) -> None:
        self._failure_count = 0
        self._opened_until = 0.0

    def record_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.threshold:
            self._opened_until = time.time() + self.cooldown
            self._failure_count = 0


def retry_call(
    func: Callable[[], T],
    *,
    attempts: int = 3,
    delay: float = 0.5,
    backoff: float = 2.0,
    exceptions: Optional[Iterable[Type[BaseException]]] = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    catches = tuple(exceptions or (Exception,))
    last_exc: Optional[BaseException] = None
    current_delay = delay
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except catches as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt == attempts:
                raise
            time.sleep(current_delay)
            current_delay *= backoff
    assert last_exc is not None
    raise last_exc

