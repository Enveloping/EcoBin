from __future__ import annotations

from dataclasses import dataclass

from .model import FactoryTestStatus, FirstBootFacts, FirstBootStage
from .state_store import FirstBootState


@dataclass(frozen=True)
class Reconciliation:
    stage: FirstBootStage
    error_code: str


_PASSED_OR_LATER = {
    FirstBootStage.FACTORY_TEST_PASSED,
    FirstBootStage.UPLINK_REQUIRED,
    FirstBootStage.UPLINK_READY,
    FirstBootStage.ENROLLMENT_REQUIRED,
    FirstBootStage.ENROLLMENT_COMPLETE,
    FirstBootStage.SEALED,
    FirstBootStage.COMPLETE,
}

_UPLINK_READY_OR_LATER = {
    FirstBootStage.UPLINK_READY,
    FirstBootStage.ENROLLMENT_REQUIRED,
    FirstBootStage.ENROLLMENT_COMPLETE,
    FirstBootStage.SEALED,
    FirstBootStage.COMPLETE,
}


def reconcile(
    facts: FirstBootFacts,
    previous: FirstBootState | None = None,
) -> Reconciliation:
    """Derive the next display/action stage from freshly observed facts.

    ``previous`` is only a progress index used to expose stable transition
    points.  Every branch above that index is revalidated first, so writing an
    advanced stage to disk cannot bypass a missing report, route, trusted time,
    or credential bundle.
    """

    if facts.sealed_exists:
        if not facts.sealed_valid:
            return Reconciliation(FirstBootStage.SEALED, "SEALED_FACT_INVALID")
        if facts.sealed_cleanup_complete:
            return Reconciliation(FirstBootStage.COMPLETE, facts.public_error_code())
        return Reconciliation(FirstBootStage.SEALED, facts.public_error_code())

    if not facts.system_prepared:
        return Reconciliation(FirstBootStage.SYSTEM_PREPARED, facts.public_error_code())

    if not facts.factory_portal_ready:
        return Reconciliation(
            FirstBootStage.FACTORY_PORTAL_READY,
            facts.public_error_code(),
        )

    if facts.factory_recovery_required or (
        facts.factory_test_status is FactoryTestStatus.RECOVERY_REQUIRED
    ):
        return Reconciliation(
            FirstBootStage.FACTORY_TEST_REQUIRED,
            "FACTORY_RECOVERY_REQUIRED",
        )

    if facts.factory_test_status is FactoryTestStatus.RUNNING:
        return Reconciliation(
            FirstBootStage.FACTORY_TEST_RUNNING,
            facts.public_error_code(),
        )

    # A PASSED word in the progress file is insufficient.  The independently
    # validated P7 report is the gate.  Until P7 exists this branch therefore
    # always stops at FACTORY_TEST_REQUIRED.
    if (
        facts.factory_test_status is not FactoryTestStatus.PASSED
        or not facts.factory_report_valid
    ):
        error = facts.public_error_code()
        if facts.factory_test_status is FactoryTestStatus.FAILED and error == "NONE":
            error = "FACTORY_TEST_FAILED"
        return Reconciliation(FirstBootStage.FACTORY_TEST_REQUIRED, error)

    previous_stage = previous.stage if previous is not None else None
    if previous_stage not in _PASSED_OR_LATER:
        return Reconciliation(FirstBootStage.FACTORY_TEST_PASSED, facts.public_error_code())

    # Once the independently validated credential bundle exists and the
    # coordinator has already reached the uplink/enrollment portion of the
    # flow, a later connectivity outage must not regress the appliance back
    # into factory networking.  Production owns offline queuing and admission;
    # first boot only needed a live uplink and trusted time to create the
    # credentials in the first place.
    if facts.enrollment_complete and previous_stage in _UPLINK_READY_OR_LATER:
        return Reconciliation(
            FirstBootStage.ENROLLMENT_COMPLETE,
            facts.public_error_code(),
        )

    if not facts.cellular_profile_active or not facts.uplink_ready:
        return Reconciliation(FirstBootStage.UPLINK_REQUIRED, facts.public_error_code())

    if previous_stage not in _UPLINK_READY_OR_LATER:
        return Reconciliation(FirstBootStage.UPLINK_READY, facts.public_error_code())

    if not facts.time_trusted:
        return Reconciliation(FirstBootStage.UPLINK_READY, "TIME_NOT_TRUSTED")

    if not facts.enrollment_complete:
        return Reconciliation(FirstBootStage.ENROLLMENT_REQUIRED, facts.public_error_code())

    # P5 observes the credential fact but does not delete K1 or manufacture a
    # seal.  P6/P8 will provide those operations and their independent facts.
    return Reconciliation(FirstBootStage.ENROLLMENT_COMPLETE, facts.public_error_code())


def gate_allows(requirement: str, facts: FirstBootFacts) -> bool:
    # Every downstream action depends on a fact produced during this boot by
    # ecobin-mcu-safe-gpio.service.  A persisted factory/enrollment/seal fact
    # must never let an ExecCondition bypass BOOT0/NRST safe initialization
    # after a restart or a failed GPIO helper invocation.
    if not facts.system_prepared:
        return False

    factory_passed = (
        facts.factory_test_status is FactoryTestStatus.PASSED
        and facts.factory_report_valid
        and not facts.factory_recovery_required
    )
    if requirement == "factory-test":
        return facts.factory_portal_ready and not facts.sealed_exists
    if requirement == "factory-test-passed":
        return factory_passed and (
            not facts.sealed_exists or facts.sealed_valid
        )
    if requirement == "enrollment":
        return factory_passed and facts.uplink_ready and facts.time_trusted
    if requirement == "handoff":
        return factory_passed and facts.enrollment_complete
    if requirement == "runtime":
        return (
            factory_passed
            and facts.enrollment_complete
            and facts.handoff_safe
            and (not facts.sealed_exists or facts.sealed_valid)
        )
    raise ValueError("unknown first-boot gate")
