from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class FirstBootStage(StrEnum):
    SYSTEM_PREPARED = "SYSTEM_PREPARED"
    FACTORY_PORTAL_READY = "FACTORY_PORTAL_READY"
    FACTORY_TEST_REQUIRED = "FACTORY_TEST_REQUIRED"
    FACTORY_TEST_RUNNING = "FACTORY_TEST_RUNNING"
    FACTORY_TEST_PASSED = "FACTORY_TEST_PASSED"
    UPLINK_REQUIRED = "UPLINK_REQUIRED"
    UPLINK_READY = "UPLINK_READY"
    ENROLLMENT_REQUIRED = "ENROLLMENT_REQUIRED"
    ENROLLMENT_COMPLETE = "ENROLLMENT_COMPLETE"
    SEALED = "SEALED"
    COMPLETE = "COMPLETE"


class FactoryTestStatus(StrEnum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def normalize_error_code(value: str | None) -> str:
    candidate = (value or "NONE").strip().upper()
    return candidate if _SAFE_ERROR_CODE.fullmatch(candidate) else "INTERNAL_ERROR"


@dataclass(frozen=True)
class FirstBootFacts:
    """Authoritative observations collected again on every reconciliation.

    No field is inferred from ``state.json``.  In particular, a persisted
    advanced stage cannot make a missing factory report, a wrong route, or
    incomplete credentials true.
    """

    system_prepared: bool = False
    factory_portal_ready: bool = False
    factory_test_status: FactoryTestStatus = FactoryTestStatus.NOT_RUN
    factory_report_valid: bool = False
    factory_recovery_required: bool = False
    cellular_profile_active: bool = False
    uplink_ready: bool = False
    time_trusted: bool = False
    enrollment_complete: bool = False
    handoff_safe: bool = False
    sealed_exists: bool = False
    sealed_valid: bool = False
    sealed_cleanup_complete: bool = False
    last_error_code: str = "NONE"

    def public_error_code(self) -> str:
        return normalize_error_code(self.last_error_code)
