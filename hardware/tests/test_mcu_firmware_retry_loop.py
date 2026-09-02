import threading

from job_safety import JobSafetyError
from main import EcoBinEdge, _is_mcu_package_retry_wait


class ExitAfterOneIteration:
    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 1


class RetryStore:
    def __init__(self):
        self.active = {
            "state": "PACKAGE_FETCH_FAILED",
            "package_ready": False,
        }

    def get_active_mcu_firmware_update(self):
        return self.active

    def get_maintenance_lock(self):
        return {"lock_type": "MCU_FIRMWARE_UPDATE"}


class RetryCommands:
    def __init__(self, store):
        self.store = store
        self.process_calls = 0

    def process_next(self):
        self.process_calls += 1
        self.store.active = {
            "state": "QUEUED",
            "package_ready": True,
        }
        return True

    def wait(self, _timeout):
        raise AssertionError("successful firmware retry must make progress")


class RetryUpdater:
    def __init__(self):
        self.process_calls = 0

    def process_active(self):
        self.process_calls += 1
        return True


def test_package_retry_wait_is_the_only_nonready_firmware_exception():
    assert _is_mcu_package_retry_wait({
        "state": "PACKAGE_FETCH_FAILED",
        "package_ready": False,
    })
    assert not _is_mcu_package_retry_wait({
        "state": "FLASHING_TARGET",
        "package_ready": True,
    })
    assert not _is_mcu_package_retry_wait(None)


def test_command_loop_processes_owner_retry_before_resuming_updater():
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge._exit_flag = ExitAfterOneIteration()
    edge.store = RetryStore()
    edge.commands = RetryCommands(edge.store)
    edge.mcu_updater = RetryUpdater()
    edge._poll_remote_support_status = lambda: 0
    edge.runtime_snapshot_requests = 0
    edge._request_runtime_snapshot = lambda: setattr(
        edge,
        "runtime_snapshot_requests",
        edge.runtime_snapshot_requests + 1,
    )

    edge._command_loop()

    assert edge.commands.process_calls == 1
    assert edge.mcu_updater.process_calls == 1
    assert edge.runtime_snapshot_requests == 1


class ExitAfterTwoIterations:
    def __init__(self):
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > 2


class PendingEventStore:
    event = {
        "mcu_boot_id": 41,
        "mcu_event_sequence": 9,
        "mcu_receive_generation": 3,
    }

    def __init__(self):
        self.processed = False
        self.failed = False

    def get_active_mcu_firmware_update(self):
        return None

    def list_pending_mcu_events(self, *, limit):
        assert limit == 20
        return [] if self.processed else [self.event]

    def mark_mcu_event_processed(self, boot_id, sequence, generation):
        assert (boot_id, sequence, generation) == (41, 9, 3)
        self.processed = True

    def mark_mcu_event_failed(self, *_args):
        self.failed = True


class TransientConfirmationCommands:
    def __init__(self):
        self.event_attempts = 0
        self.wait_calls = 0

    def process_mcu_event(self, _event):
        self.event_attempts += 1
        if self.event_attempts == 1:
            raise JobSafetyError(
                "JOB_GATE_UNAVAILABLE",
                "temporary updater socket outage",
            )

    def process_next(self):
        return False

    def wait(self, _timeout):
        self.wait_calls += 1


class NoopWorkReconciler:
    def expire_fixed_frame_work(self):
        return False

    def reconcile_pending_job_safety_completion(self):
        return False

    def reconcile_pending_physical_action_confirmations(self):
        return False

    def reconcile_pre_action_job_safety_failure(self):
        return False

    def reconcile_orphan_granted_job_permits(self):
        return 0


class NoopPoller:
    def poll(self):
        return {"state_changed": False}


def test_transient_job_receipt_failure_keeps_mcu_event_pending_for_retry():
    edge = EcoBinEdge.__new__(EcoBinEdge)
    edge._exit_flag = ExitAfterTwoIterations()
    edge.store = PendingEventStore()
    edge.commands = TransientConfirmationCommands()
    edge.work = NoopWorkReconciler()
    edge.mcu_updater = None
    edge._poll_remote_support_status = lambda: 0
    edge._uart_recovering = threading.Event()
    edge.fixed_frame_health_recovery = NoopPoller()
    edge.device_entry_url_refresh = type(
        "NoopRefresh",
        (),
        {"poll": lambda self: None},
    )()
    edge._request_runtime_snapshot = lambda: None

    edge._command_loop()

    assert edge.commands.event_attempts == 2
    assert edge.commands.wait_calls == 1
    assert edge.store.processed is True
    assert edge.store.failed is False
