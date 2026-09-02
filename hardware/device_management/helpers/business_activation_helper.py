"""Socket-activated fixed business-runtime primitives for the local updater."""

from __future__ import annotations

try:
    import pwd
except ImportError:  # pragma: no cover - deployment is Linux-only
    pwd = None  # type: ignore[assignment]

from .business_activation_primitives import BusinessActivationPrimitives
from .privileged_control import (
    HelperAction,
    HelperPolicy,
    serve_systemd_connection,
)

PROTOCOL_NAME = "ecobin.business-activation-helper.control"
POLICY = HelperPolicy(
    protocol_name=PROTOCOL_NAME,
    component="BUSINESS_ACTIVATION_HELPER",
    fixed_configuration={
        "businessService": "ecobin-hardware.service",
        "businessReleaseRoot": "/opt/ecobin/business/releases",
        "businessCurrentLink": "/opt/ecobin/business/current",
        "businessDatabase": "/var/lib/ecobin/business/edge.db",
        "stagingRoot": "/var/lib/ecobin/updater/staging",
        "snapshotRoot": "/var/lib/ecobin/privileged/business-snapshots",
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
        "CLEAN_BUSINESS_STAGING",
    ),
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
    primitives = BusinessActivationPrimitives(
        business_uid=business.pw_uid,
        business_gid=business.pw_gid,
        updater_uid=updater.pw_uid,
        updater_gid=updater.pw_gid,
    )
    update_fields = frozenset({"updateUid"})
    release_fields = frozenset({"updateUid", "releaseUid"})
    return {
        "GET_BUSINESS_RUNTIME_STATUS": HelperAction(
            primitives.status,
            frozenset(),
        ),
        "STOP_BUSINESS_RUNTIME": HelperAction(primitives.stop, update_fields),
        "START_BUSINESS_RUNTIME": HelperAction(primitives.start, update_fields),
        "SNAPSHOT_BUSINESS_DATABASE": HelperAction(
            primitives.snapshot_database,
            update_fields,
        ),
        "RESTORE_BUSINESS_DATABASE": HelperAction(
            primitives.restore_database,
            update_fields,
        ),
        "INSTALL_BUSINESS_RELEASE": HelperAction(
            primitives.install_release,
            release_fields,
        ),
        "ACTIVATE_BUSINESS_RELEASE": HelperAction(
            primitives.activate_release,
            release_fields,
        ),
        "ROLLBACK_BUSINESS_RELEASE": HelperAction(
            primitives.rollback_release,
            release_fields,
        ),
        "CLEAN_BUSINESS_STAGING": HelperAction(
            primitives.cleanup_staging,
            update_fields,
        ),
    }


def main() -> int:
    serve_systemd_connection(POLICY, build_actions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
