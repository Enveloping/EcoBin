"""Zero is a valid measured weight in the current autonomous C runtime."""

from hardware.tests.test_mcu_simplified_execution import (
    closed_measurement,
    final,
    library,
    runtime,
    select,
    setup,
    state,
    tick,
)
from hardware.tests.test_mcu_work_preparation import take_samples


def test_delivery_completion_accepts_zero_preweight_only_for_active_flow(
        runtime) -> None:
    delivery, _, start, now = setup(runtime, initial=False)
    now = take_samples(runtime, [0] * 5, start=now)
    now = tick(runtime, now, 0)

    # A successful zero-valued measurement is not the same fact as a missing
    # measurement. The MCU therefore enters the active door flow.
    assert state(runtime, start, now)["phase"] == "DELIVERY_OPEN_COMMAND"

    now = closed_measurement(runtime, start, now)
    now = take_samples(runtime, [500] * 5, start=now, measurement=2)
    assert select(runtime, delivery, now)
    result = final(runtime, start, now)
    assert result["initialKind"] == "STABLE_MEAN"
    assert result["initialWeightGrams"] == 0
    assert result["finalWeightGrams"] == 500
    assert result["finishReason"] == "DELIVERY_END"
