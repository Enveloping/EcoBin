"""A stoppable, wakeable single-slot worker with foreground completions."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerCompletion:
    key: str
    value: Any = None
    error: Exception | None = None


class SingleSlotWorker:
    """Run one external operation while keeping all database writes on owner."""

    def __init__(self, name: str, *, wake_owner: Callable[[], None] | None = None):
        self._name = name
        self._wake_owner = wake_owner
        self._condition = threading.Condition()
        self._task: tuple[str, Callable, tuple] | None = None
        self._completion: WorkerCompletion | None = None
        self._running = False
        self._stopping = False
        self._accept_completions = True
        self._thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )
        self._thread.start()

    @property
    def busy(self) -> bool:
        with self._condition:
            return bool(
                self._task is not None
                or self._running
                or self._completion is not None
            )

    def submit(self, key: str, operation: Callable, *args) -> bool:
        if not isinstance(key, str) or not key or not callable(operation):
            raise ValueError("worker task requires an identity and operation")
        with self._condition:
            if (
                self._stopping
                or self._task is not None
                or self._running
                or self._completion is not None
            ):
                return False
            self._task = (key, operation, args)
            self._condition.notify()
            return True

    def take_completion(self) -> WorkerCompletion | None:
        with self._condition:
            completion = self._completion
            self._completion = None
            return completion

    def close(self, *, timeout_s: float = 2.0) -> bool:
        if not isinstance(timeout_s, (int, float)) or timeout_s < 0:
            raise ValueError("worker close timeout must be non-negative")
        with self._condition:
            self._stopping = True
            self._accept_completions = False
            self._task = None
            self._completion = None
            self._condition.notify_all()
        self._thread.join(timeout_s)
        return not self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._task is None and not self._stopping:
                    self._condition.wait()
                if self._stopping and self._task is None:
                    return
                key, operation, args = self._task
                self._task = None
                self._running = True
            try:
                completion = WorkerCompletion(
                    key=key,
                    value=operation(*args),
                )
            except Exception as error:
                completion = WorkerCompletion(key=key, error=error)
            with self._condition:
                self._running = False
                if self._accept_completions and not self._stopping:
                    self._completion = completion
                self._condition.notify_all()
            if self._wake_owner is not None:
                self._wake_owner()
