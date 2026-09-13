"""A single-slot background wait for the permanent job authority.

The creating foreground thread owns every public operation. The daemon worker
only executes the supplied safety RPC closure: it is never given a serial link
or a database, and it never decides which business may start or finish. The
caller must verify the returned original identity before using a completion.

There is no queue beyond one outstanding request, and no automatic retry here.
Closing drops unread results without waiting for, cancelling, or undoing an
external operation that the worker has already taken.
"""

from dataclasses import dataclass
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class RpcCompletion:
    identity: tuple
    value: Any = None
    error: BaseException | None = None


class NativeJobRpc:
    """Keep UART polling independent of one bounded, pending ledger request."""

    def __init__(self):
        self._owner = threading.current_thread()
        self._condition = threading.Condition()
        self._pending: tuple[tuple, Callable[[], Any]] | None = None
        self._running = False
        self._completion: RpcCompletion | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="native-job-rpc")
        self._thread.start()

    def _require_owner(self) -> None:
        if threading.current_thread() is not self._owner:
            raise RuntimeError("native job RPC must be called by its foreground owner")

    @property
    def busy(self) -> bool:
        """Include an executing request or an unread completion, even at close."""
        self._require_owner()
        with self._condition:
            return self._running or self._pending is not None or self._completion is not None

    def submit(self, identity: tuple, operation: Callable[[], Any]) -> bool:
        """Accept once; busy/closed slots do not replace or queue a request."""
        self._require_owner()
        if not isinstance(identity, tuple):
            raise TypeError("native RPC identity must be a hashable tuple")
        hash(identity)
        if not callable(operation):
            raise TypeError("native RPC operation must be callable")
        with self._condition:
            if self._closed or self._running or self._pending is not None or self._completion is not None:
                return False
            self._pending = (identity, operation)
            self._condition.notify()
            return True

    def take_completed(self) -> RpcCompletion | None:
        """Consume one finished result without waiting for an external reply."""
        self._require_owner()
        with self._condition:
            completed, self._completion = self._completion, None
            return completed

    def close(self) -> None:
        """Stop accepting work immediately; a running RPC may still commit."""
        self._require_owner()
        with self._condition:
            self._closed = True
            self._pending = None
            self._completion = None
            self._condition.notify()
        # Deliberately no join: the operating system/local RPC response timeout
        # belongs to the RPC client, not to UART shutdown. The worker is daemon.

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                identity, operation = self._pending
                self._pending = None
                self._running = True
            value, error = None, None
            try:
                value = operation()
            except BaseException as raised:
                # Include worker-local SystemExit/KeyboardInterrupt: neither may
                # silently kill this worker and strand the sole occupied slot.
                error = raised
            finally:
                operation = None
            with self._condition:
                self._running = False
                if self._closed:
                    return
                self._completion = RpcCompletion(identity=identity, value=value, error=error)
                self._condition.notify_all()
            # Once consumed, a completion must not remain alive solely through
            # this idle worker's locals (especially an exception traceback).
            identity = value = error = None
