"""Stage-six business-release primitives authorized by the updater journal."""

from __future__ import annotations

try:
    import pwd
except ImportError:  # pragma: no cover - deployment is Linux-only
    pwd = None  # type: ignore[assignment]

from local_control import LocalControlActionError

from .business_activation_primitives import (
    CANDIDATE_BUSINESS_SERVICE,
    UPDATABLE_BUSINESS_SERVICE,
    BusinessActivationPrimitives,
)
from .privileged_control import HelperAction, HelperPolicy, serve_systemd_connection
from .updater_mutation_authorizer import build_authorizer


PROTOCOL_NAME = "ecobin.business-release-activation-helper.control"
COMPONENT = "BUSINESS_RELEASE_ACTIVATION_CANDIDATE_HELPER"
SERVICE_CONTROL_TIMEOUT_SECONDS = 190
POLICY = HelperPolicy(
    protocol_name=PROTOCOL_NAME,
    component=COMPONENT,
    fixed_configuration={
        "businessService": UPDATABLE_BUSINESS_SERVICE,
        "bridgeBusinessService": CANDIDATE_BUSINESS_SERVICE,
        "businessReleaseRoot": "/opt/ecobin/business/releases",
        "businessCurrentLink": "/opt/ecobin/business/current",
        "businessDatabase": "/var/lib/ecobin/business/edge.db",
        "stagingRoot": "/var/lib/ecobin/updater/staging",
        "snapshotRoot": "/var/lib/ecobin/privileged/business-snapshots",
        "serviceControlTimeoutSeconds": SERVICE_CONTROL_TIMEOUT_SECONDS,
    },
    primitive_actions=(
        "GET_BUSINESS_RUNTIME_STATUS",
        "STOP_BUSINESS_RUNTIME",
        "START_BUSINESS_RUNTIME",
        "SNAPSHOT_BUSINESS_DATABASE",
        "RESTORE_BUSINESS_DATABASE",
        "INSTALL_BUSINESS_RELEASE",
        "ACTIVATE_BUSINESS_RELEASE",
        "ROLLBACK_BUSINESS_RELEASE",
        "DEACTIVATE_BUSINESS_RELEASE",
        "STOP_BUSINESS_BRIDGE",
        "START_BUSINESS_BRIDGE",
    ),
    stage=6,
    mutation_enabled=True,
)


class BusinessReleaseCandidateActions:
    """Keep the image bridge and replaceable runtime mutually exclusive."""

    def __init__(
        self,
        updatable: BusinessActivationPrimitives,
        bridge: BusinessActivationPrimitives,
    ) -> None:
        self.updatable = updatable
        self.bridge = bridge

    def status(self, payload: dict) -> dict:
        updatable = self.updatable.status(payload)
        bridge = self.bridge.status(payload)
        updatable_state = updatable["businessRuntimeState"]
        bridge_state = bridge["businessRuntimeState"]
        if updatable_state == "ACTIVE" and bridge_state == "ACTIVE":
            raise LocalControlActionError(
                "BUSINESS_SERVICE_CONFLICT",
                "both fixed business services are active",
            )
        return {
            **updatable,
            "bridgeBusinessRuntimeState": bridge_state,
        }

    def stop_updatable(self, payload: dict) -> dict:
        return self.updatable.stop(payload)

    def stop_bridge(self, payload: dict) -> dict:
        return self.bridge.stop(payload)

    def stop_selected(self, payload: dict) -> dict:
        status = self.status(payload)
        if status["currentReleaseUid"] is not None:
            self._require_inactive(self.bridge, "image bridge")
            return self.updatable.stop(payload)
        self._require_inactive(self.updatable, "replaceable business runtime")
        return self.bridge.stop(payload)

    def start_updatable(self, payload: dict) -> dict:
        self._require_inactive(self.bridge, "image bridge")
        status = self.updatable.status(payload)
        if status["currentReleaseUid"] is None:
            raise LocalControlActionError(
                "BUSINESS_RELEASE_NOT_ACTIVE",
                "replaceable business current release is absent",
            )
        return self.updatable.start(payload)

    def start_bridge(self, payload: dict) -> dict:
        self._require_inactive(self.updatable, "replaceable business runtime")
        status = self.updatable.status(payload)
        if status["currentReleaseUid"] is not None:
            raise LocalControlActionError(
                "BUSINESS_RELEASE_STILL_ACTIVE",
                "replaceable business current release is still active",
            )
        return self.bridge.start(payload)

    def start_selected(self, payload: dict) -> dict:
        status = self.status(payload)
        if status["currentReleaseUid"] is not None:
            self._require_inactive(self.bridge, "image bridge")
            return self.updatable.start(payload)
        self._require_inactive(self.updatable, "replaceable business runtime")
        return self.bridge.start(payload)

    def snapshot_database(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.snapshot_database(payload)

    def restore_database(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.restore_database(payload)

    def install_release(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.install_release(payload)

    def activate_release(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.activate_release(payload)

    def rollback_release(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.rollback_release(payload)

    def deactivate_release(self, payload: dict) -> dict:
        self._require_both_inactive()
        return self.updatable.deactivate_release(payload)

    def _require_both_inactive(self) -> None:
        self._require_inactive(self.updatable, "replaceable business runtime")
        self._require_inactive(self.bridge, "image bridge")

    @staticmethod
    def _require_inactive(
        primitives: BusinessActivationPrimitives,
        label: str,
    ) -> None:
        if primitives.service_state() != "INACTIVE":
            raise LocalControlActionError(
                "BUSINESS_RUNTIME_ACTIVE",
                f"{label} must be inactive",
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
    updatable = BusinessActivationPrimitives(
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        updater_uid=updater.pw_uid,
        updater_gid=updater.pw_gid,
        business_service=UPDATABLE_BUSINESS_SERVICE,
        service_control_timeout_seconds=SERVICE_CONTROL_TIMEOUT_SECONDS,
    )
    bridge = BusinessActivationPrimitives(
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        updater_uid=updater.pw_uid,
        updater_gid=updater.pw_gid,
        business_service=CANDIDATE_BUSINESS_SERVICE,
        service_control_timeout_seconds=SERVICE_CONTROL_TIMEOUT_SECONDS,
    )
    actions = BusinessReleaseCandidateActions(updatable, bridge)
    identity_fields = frozenset({"updateUid", "actionUid"})
    release_fields = frozenset({"updateUid", "actionUid", "releaseUid"})
    install_fields = frozenset(
        {
            "updateUid",
            "actionUid",
            "releaseUid",
            "packageSha256",
            "versionName",
            "releaseSequence",
        }
    )
    return {
        "GET_BUSINESS_RUNTIME_STATUS": HelperAction(
            actions.status, identity_fields
        ),
        "STOP_BUSINESS_RUNTIME": HelperAction(
            actions.stop_updatable, identity_fields
        ),
        "START_BUSINESS_RUNTIME": HelperAction(
            actions.start_updatable, identity_fields
        ),
        "SNAPSHOT_BUSINESS_DATABASE": HelperAction(
            actions.snapshot_database, identity_fields
        ),
        "RESTORE_BUSINESS_DATABASE": HelperAction(
            actions.restore_database, identity_fields
        ),
        "INSTALL_BUSINESS_RELEASE": HelperAction(
            actions.install_release, install_fields
        ),
        "ACTIVATE_BUSINESS_RELEASE": HelperAction(
            actions.activate_release, release_fields
        ),
        "ROLLBACK_BUSINESS_RELEASE": HelperAction(
            actions.rollback_release, release_fields
        ),
        "DEACTIVATE_BUSINESS_RELEASE": HelperAction(
            actions.deactivate_release, release_fields
        ),
        "STOP_BUSINESS_BRIDGE": HelperAction(
            actions.stop_bridge, identity_fields
        ),
        "START_BUSINESS_BRIDGE": HelperAction(
            actions.start_bridge, identity_fields
        ),
    }


def _authorizer_factory(_updater_uid: int):
    return build_authorizer(COMPONENT)


def main() -> int:
    serve_systemd_connection(POLICY, build_actions, _authorizer_factory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
