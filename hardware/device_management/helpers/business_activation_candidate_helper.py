"""Stage-four authorized primitives for the low-privilege business service."""

from __future__ import annotations

try:
    import pwd
except ImportError:  # pragma: no cover - deployment is Linux-only
    pwd = None  # type: ignore[assignment]

from .business_activation_primitives import (
    CANDIDATE_BUSINESS_SERVICE,
    UPDATABLE_BUSINESS_SERVICE,
    BusinessActivationPrimitives,
)
from .business_release_activation_candidate_helper import (
    BusinessReleaseCandidateActions,
)
from .privileged_control import HelperAction, HelperPolicy, serve_systemd_connection
from .updater_mutation_authorizer import build_authorizer


PROTOCOL_NAME = "ecobin.business-activation-helper.control"
COMPONENT = "BUSINESS_ACTIVATION_CANDIDATE_HELPER"
CANDIDATE_SERVICE_CONTROL_TIMEOUT_SECONDS = 190
POLICY = HelperPolicy(
    protocol_name=PROTOCOL_NAME,
    component=COMPONENT,
    fixed_configuration={
        "businessServices": [
            CANDIDATE_BUSINESS_SERVICE,
            UPDATABLE_BUSINESS_SERVICE,
        ],
        "serviceControlTimeoutSeconds": CANDIDATE_SERVICE_CONTROL_TIMEOUT_SECONDS,
    },
    primitive_actions=(
        "STOP_BUSINESS_RUNTIME",
        "START_BUSINESS_RUNTIME",
    ),
    stage=4,
    mutation_enabled=True,
)


def build_actions(updater_uid: int) -> dict[str, HelperAction]:
    if pwd is None:
        raise RuntimeError("system account lookup is unavailable")
    try:
        business = pwd.getpwnam("ecobin-business")
        updater = pwd.getpwnam("ecobin-updater")
    except KeyError as error:
        raise RuntimeError("required EcoBin runtime account does not exist") from error
    if updater.pw_uid != updater_uid:
        raise RuntimeError("resolved ecobin-updater identity changed")
    bridge = BusinessActivationPrimitives(
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        updater_uid=updater.pw_uid,
        updater_gid=updater.pw_gid,
        business_service=CANDIDATE_BUSINESS_SERVICE,
        service_control_timeout_seconds=CANDIDATE_SERVICE_CONTROL_TIMEOUT_SECONDS,
    )
    updatable = BusinessActivationPrimitives(
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        updater_uid=updater.pw_uid,
        updater_gid=updater.pw_gid,
        business_service=UPDATABLE_BUSINESS_SERVICE,
        service_control_timeout_seconds=CANDIDATE_SERVICE_CONTROL_TIMEOUT_SECONDS,
    )
    primitives = BusinessReleaseCandidateActions(updatable, bridge)
    mutation_fields = frozenset({"updateUid", "actionUid"})
    return {
        "STOP_BUSINESS_RUNTIME": HelperAction(
            primitives.stop_selected, mutation_fields
        ),
        "START_BUSINESS_RUNTIME": HelperAction(
            primitives.start_selected, mutation_fields
        ),
    }


def _authorizer_factory(_updater_uid: int):
    return build_authorizer(COMPONENT)


def main() -> int:
    serve_systemd_connection(POLICY, build_actions, _authorizer_factory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
