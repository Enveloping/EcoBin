"""Single-slot permanent-ledger waits, with no sockets or device access."""

from dataclasses import FrozenInstanceError
import threading

import pytest

from native_job_rpc import NativeJobRpc


@pytest.fixture
def rpc():
    worker = NativeJobRpc()
    try:
        yield worker
    finally:
        worker.close()
        worker._thread.join(timeout=1)
        assert not worker._thread.is_alive()


def published(rpc):
    # The public API intentionally has no separate completion-wait operation:
    # production must keep polling UART, not wait for this condition.
    with rpc._condition:
        assert rpc._condition.wait_for(lambda: rpc._completion is not None, timeout=1)


def test_idle_has_no_completion_or_pending_request(rpc):
    assert not rpc.busy
    assert rpc.take_completed() is None


def test_operation_runs_on_one_reused_daemon_thread_and_returns_original_value(rpc):
    owner = threading.current_thread()
    threads = []
    value = {"state": "ACTIVE"}

    def operation():
        threads.append(threading.current_thread())
        return value

    for identity in (("permit", "first"), ("permit", "second")):
        assert rpc.submit(identity, operation)
        published(rpc)
        completed = rpc.take_completed()
        assert completed.identity == identity
        assert completed.value is value
        assert completed.error is None
        assert not rpc.busy
    assert threads[0] is threads[1] is rpc._thread
    assert threads[0] is not owner
    assert threads[0].daemon


def test_running_request_rejects_duplicate_and_other_request_without_queueing(rpc):
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocked():
        calls.append("original")
        started.set()
        assert release.wait(2)
        return "done"

    assert rpc.submit(("original",), blocked)
    assert started.wait(1)
    try:
        assert rpc.busy
        assert rpc.take_completed() is None
        assert not rpc.submit(("original",), lambda: calls.append("duplicate"))
        assert not rpc.submit(("another",), lambda: calls.append("another"))
    finally:
        release.set()
    published(rpc)
    assert rpc.take_completed().value == "done"
    assert calls == ["original"]


def test_completed_unconsumed_result_still_owns_the_only_slot(rpc):
    assert rpc.submit(("original",), lambda: 7)
    published(rpc)
    assert rpc.busy
    assert not rpc.submit(("next",), lambda: pytest.fail("unconsumed result was replaced"))
    completed = rpc.take_completed()
    assert completed.identity == ("original",)
    assert completed.value == 7
    assert rpc.take_completed() is None
    assert not rpc.busy
    assert rpc.submit(("next",), lambda: 8)
    published(rpc)
    assert rpc.take_completed().value == 8


@pytest.mark.parametrize("error", [RuntimeError("unavailable"), ValueError("invalid"),
    KeyboardInterrupt("worker interrupted"), SystemExit("worker stopped")])
def test_original_exception_is_returned_without_retry_and_worker_survives(rpc, error):
    calls = []

    def fail():
        calls.append("once")
        raise error

    assert rpc.submit(("failing",), fail)
    published(rpc)
    completed = rpc.take_completed()
    assert completed.identity == ("failing",)
    assert completed.error is error
    assert completed.value is None
    assert calls == ["once"]
    assert rpc.submit(("following",), lambda: "alive")
    published(rpc)
    assert rpc.take_completed().value == "alive"


def test_none_is_a_completed_value_not_an_absent_completion(rpc):
    assert rpc.submit(("complete-job",), lambda: None)
    published(rpc)
    completed = rpc.take_completed()
    assert completed is not None
    assert completed.value is None
    assert completed.error is None
    with pytest.raises(FrozenInstanceError):
        completed.identity = ("different-business",)


@pytest.mark.parametrize("action", ["submit", "busy", "take_completed", "close"])
def test_public_api_rejects_non_owner_thread(rpc, action):
    errors = []

    def wrong_thread():
        try:
            if action == "busy":
                _ = rpc.busy
            elif action == "submit":
                rpc.submit(("wrong-owner",), lambda: None)
            else:
                getattr(rpc, action)()
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=wrong_thread, daemon=True)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert "owner" in str(errors[0])
    assert not rpc.busy
    assert rpc.submit(("correct-owner",), lambda: None)
    published(rpc)
    rpc.take_completed()


def test_worker_operation_cannot_reenter_owner_api(rpc):
    assert rpc.submit(("reentrant",), rpc.take_completed)
    published(rpc)
    completed = rpc.take_completed()
    assert isinstance(completed.error, RuntimeError)
    assert "owner" in str(completed.error)


def test_close_idle_is_idempotent_and_rejects_new_requests(rpc):
    rpc.close()
    rpc.close()
    assert not rpc.submit(("after-close",), lambda: pytest.fail("closed worker ran operation"))
    assert rpc.take_completed() is None
    assert not rpc.busy


def test_close_discards_completed_unconsumed_result(rpc):
    assert rpc.submit(("completed",), lambda: {"state": "COMPLETED"})
    published(rpc)
    rpc.close()
    assert rpc.take_completed() is None
    assert not rpc.busy
    assert not rpc.submit(("another",), lambda: None)


def test_close_discards_a_request_not_yet_taken_by_worker(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_run = NativeJobRpc._run
    calls = []

    def paused_worker(self):
        entered.set()
        assert release.wait(2)
        original_run(self)

    monkeypatch.setattr(NativeJobRpc, "_run", paused_worker)
    rpc = NativeJobRpc()
    try:
        assert entered.wait(1)
        assert rpc.submit(("queued",), lambda: calls.append("unexpected execution"))
        assert rpc.busy
        assert not rpc.submit(("another",), lambda: None)
        rpc.close()
        assert not rpc.busy
        assert rpc.take_completed() is None
    finally:
        rpc.close()
        release.set()
        rpc._thread.join(timeout=1)
    assert not rpc._thread.is_alive()
    assert calls == []


def test_close_returns_while_rpc_still_runs_and_does_not_cancel_its_effect(rpc):
    started, release, watchdog = threading.Event(), threading.Event(), threading.Event()
    external_facts = []

    def blocked_external_operation():
        started.set()
        if not release.wait(2):
            watchdog.set()
        external_facts.append("the original external fact committed")
        return {"state": "ACTIVE"}

    assert rpc.submit(("original",), blocked_external_operation)
    assert started.wait(1)
    try:
        rpc.close()
        assert not watchdog.is_set(), "close waited for the external operation"
        assert rpc._thread.is_alive()
        assert rpc.busy  # An external operation is still running, not cancelled.
        assert rpc.take_completed() is None
        assert not rpc.submit(("replacement",), lambda: None)
    finally:
        release.set()
        rpc._thread.join(timeout=1)
    assert not rpc._thread.is_alive()
    assert not rpc.busy
    assert rpc.take_completed() is None
    assert external_facts == ["the original external fact committed"]


@pytest.mark.parametrize("identity", [None, "uid", ["uid"], (["mutable"],)])
def test_invalid_identity_cannot_occupy_the_slot(rpc, identity):
    with pytest.raises(TypeError):
        rpc.submit(identity, lambda: None)
    assert not rpc.busy


def test_non_callable_operation_cannot_occupy_the_slot(rpc):
    with pytest.raises(TypeError):
        rpc.submit(("original",), None)
    assert not rpc.busy
