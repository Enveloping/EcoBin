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
