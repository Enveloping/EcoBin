"""START-only rc.23 work fixture for native recovery/issue tests."""
from contextlib import contextmanager


@contextmanager
def autonomous_active_case(runtime, tmp_path, *, clean=None, clean_work=None,
                           cloud_command_factory=None):
    """Pause current autonomous work after its first door/lock action.

    The only cloud/native control command is START. Process queries below
    retain facts the MCU already produced and never create an old action
    binding or send a deprecated per-action command.
    """
    import uart2_protocol as uart
    from hardware.tests.test_mcu_simplified_execution import tick
    from hardware.tests.test_native_configuration import inputs
    from hardware.tests.test_simplified_mcu_pi_business import real_work
    from hardware.tests.test_native_work_recovery import RecoveryWire

    if clean is None:
        clean = clean_work
    if not isinstance(clean, bool):
        raise ValueError("autonomous active case requires its work kind")
    if cloud_command_factory is not None and cloud_command_factory.__name__ != "original_command":
        raise ValueError("autonomous active case only accepts the production cloud command")
    with real_work(runtime, tmp_path, clean) as case:
        case.clean = clean
        case.occupancy = case.store.get_work_slot()
        case.execution = case.cleanup if clean else case.delivery
        case.now = case.wire.now
        wire = RecoveryWire(case, runtime)
        case.wire = wire
        initial_name = "WORK_PREUNLOCK_WEIGHT_READY" if clean else "WORK_PREOPEN_WEIGHT_READY"
        initial_step = 0 if clean else 1
        saved = wire.custody(initial_name, initial_step)
        case.initial = uart.decode_payload(saved["message_name"], saved["payload"])
        case.scope = saved["scope"]
        if clean:
            wire.now = tick(runtime, wire.now, inputs()["device"]["cleanSolenoidPulseMs"])
        else:
            for duration in (100, case.start["deliveryAutoCloseMs"], 100):
                wire.now = tick(runtime, wire.now, duration)
        case.now = wire.now
        yield case
