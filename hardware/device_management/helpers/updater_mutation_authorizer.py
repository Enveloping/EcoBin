"""Authorize one candidate root primitive through the durable updater ledger."""

from __future__ import annotations

from typing import Any

from local_control import (
    LocalControlActionError,
    LocalControlClient,
    LocalControlRemoteError,
    LocalControlUnavailable,
    canonical_local_payload_sha256,
)


UPDATER_SOCKET = "/run/ecobin/updater/control.sock"
UPDATER_PROTOCOL_NAME = "ecobin.updater.control"


class UpdaterMutationAuthorizer:
    """Require the updater to consume one pre-journaled action authorization."""

    def __init__(
        self,
        helper_component: str,
        *,
        socket_path: str = UPDATER_SOCKET,
        client: LocalControlClient | None = None,
    ) -> None:
        if not isinstance(helper_component, str) or not helper_component:
            raise ValueError("helper component must be configured")
        self.helper_component = helper_component
        self._client = client or LocalControlClient(
            socket_path,
            protocol_name=UPDATER_PROTOCOL_NAME,
        )

    def authorize(self, action: str, payload: dict[str, Any]) -> None:
        update_uid = payload.get("updateUid")
        action_uid = payload.get("actionUid")
        if not isinstance(update_uid, str) or not isinstance(action_uid, str):
            raise LocalControlActionError(
                "REQUEST_INVALID",
                "candidate helper mutation identities are missing",
            )
        request = {
            "helperComponent": self.helper_component,
            "helperAction": action,
            "updateUid": update_uid,
            "actionUid": action_uid,
            "payloadSha256": canonical_local_payload_sha256(payload),
        }
        try:
            result = self._client.request(
                "AUTHORIZE_PRIVILEGED_HELPER_ACTION",
                request,
            )
        except LocalControlRemoteError as error:
            raise LocalControlActionError(error.code, error.message) from error
        except LocalControlUnavailable as error:
            raise LocalControlActionError(
                "HELPER_AUTHORIZATION_UNAVAILABLE",
                "durable updater authorization could not be confirmed",
            ) from error
        if (
            result.get("authorized") is not True
            or result.get("updateUid") != update_uid
            or result.get("actionUid") != action_uid
            or result.get("helperComponent") != self.helper_component
            or result.get("helperAction") != action
            or result.get("payloadSha256") != request["payloadSha256"]
        ):
            raise LocalControlActionError(
                "HELPER_AUTHORIZATION_INVALID",
                "durable updater authorization response is invalid",
            )


def build_authorizer(helper_component: str):
    """Return the callback shape consumed by ``OneShotPrivilegedHelper``."""

    authorizer = UpdaterMutationAuthorizer(helper_component)
    return authorizer.authorize
