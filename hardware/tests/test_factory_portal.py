from __future__ import annotations

from http import HTTPStatus
import io
import json
from pathlib import Path
import re
from typing import Any

import pytest

from factory.acceptance_portal_client import AcceptancePortalClientError
from factory.network import FACTORY_ADDRESS, FACTORY_PORT
from factory.portal import (
    BoundedHeaderReader,
    FactorySealPortalAdapter,
    FactoryPortalServer,
    MAX_HEADER_BYTES,
    MAX_HEADER_COUNT,
    MAX_HEADER_LINE_BYTES,
    MAX_REQUEST_LINE_BYTES,
    PortalApplication,
    PortalSnapshotProvider,
    RateLimiter,
    RequestHeaderLimitExceeded,
    SnapshotPaths,
    build_server,
    security_headers,
)
from factory_seal.errors import FactorySealPortalError
from factory_seal.runtime_health import active_runtime_services
from first_boot.factory_flow import _SEAL_STATUS_CODES
from first_boot.model import FactoryTestStatus, FirstBootStage


class FixedSnapshot:
    def snapshot(self) -> dict[str, Any]:
        return {"schemaVersion": 1, "readOnly": True, "stage": "FACTORY_PORTAL_READY"}


class FakeActionClient:
    def __init__(self, status: dict[str, Any] | None = None) -> None:
        self.current = status or {
            "status": "RUNNING",
            "revision": 3,
            "cameraReview": None,
        }
        self.requests: list[dict[str, Any]] = []
        self.error: AcceptancePortalClientError | None = None

    def status(self) -> dict[str, Any]:
        return dict(self.current)

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return {"status": "RUNNING", "revision": 4, "idempotent": False}


class FakeSealPortal:
    def __init__(self, *, allowed: bool = False) -> None:
        self.allowed = allowed
        self.confirmed: list[str] = []
        self.acknowledged: list[str] = []
        self.status_code = "SEAL_READY" if allowed else "CLOUD_ACCEPTANCE_REQUIRED"
        self.error: AcceptancePortalClientError | None = None

    def status(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return {
            "authorized": self.allowed,
            "confirmAllowed": self.allowed,
            "statusCode": self.status_code,
            "acceptanceGeneration": 1 if self.allowed else None,
            "authorizationBindingSha256": "a" * 64 if self.allowed else None,
            "runtimeServices": active_runtime_services(),
        }

    def confirm(self, operator_confirmation_uid: str) -> dict[str, Any]:
        self.confirmed.append(operator_confirmation_uid)
        return {
            "authorized": True,
            "confirmAllowed": False,
            "statusCode": "SEALED_RESPONSE_PENDING",
            "acceptanceGeneration": 1,
            "authorizationBindingSha256": "a" * 64,
            "runtimeServices": active_runtime_services(),
        }

    def acknowledge_presented(
        self, operator_confirmation_uid: str
    ) -> dict[str, Any]:
        self.acknowledged.append(operator_confirmation_uid)
        return {
            "authorized": True,
            "confirmAllowed": False,
            "statusCode": "SEALED_RESPONSE_PENDING",
            "acceptanceGeneration": 1,
            "authorizationBindingSha256": "a" * 64,
            "runtimeServices": active_runtime_services(),
        }


def test_portal_reads_release_identity_from_the_public_projection() -> None:
    paths = SnapshotPaths()

    assert paths.image_release == Path(
        "/usr/share/ecobin/image-release.json"
    )


def _headers(**overrides: str) -> dict[str, str]:
    result = {"Host": FACTORY_ADDRESS, "Sec-Fetch-Site": "same-origin"}
    result.update(overrides)
    return result


def _action_headers(body: bytes, **overrides: str) -> dict[str, str]:
    result = _headers(
        Origin=f"http://{FACTORY_ADDRESS}",
        **{
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-EcoBin-Factory-Action": "1",
        },
    )
    result.update(overrides)
    return result


def test_status_endpoint_is_same_origin_read_only_json() -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    response = application.handle(
        "GET", "/api/v1/status", _headers(), "10.42.0.20"
    )

    assert response.status == HTTPStatus.OK
    assert response.content_type == "application/json; charset=utf-8"
    assert json.loads(response.body) == {
        "schemaVersion": 1,
        "readOnly": True,
        "stage": "FACTORY_PORTAL_READY",
    }


def test_explicit_default_port_is_still_the_same_local_origin() -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    response = application.handle(
        "GET",
        "/api/v1/status",
        _headers(Origin=f"http://{FACTORY_ADDRESS}:{FACTORY_PORT}"),
        "10.42.0.20",
    )

    assert response.status == HTTPStatus.OK


def test_rate_limit_is_per_client_and_recovers_after_window() -> None:
    now = [100.0]
    limiter = RateLimiter(maximum_requests=2, window_seconds=10, clock=lambda: now[0])

    assert limiter.allow("10.42.0.20") is True
    assert limiter.allow("10.42.0.20") is True
    assert limiter.allow("10.42.0.20") is False
    assert limiter.allow("10.42.0.21") is True

    now[0] = 111.0
    assert limiter.allow("10.42.0.20") is True


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({"Host": "evil.example"}, "HOST_FORBIDDEN"),
        (
            {"Host": FACTORY_ADDRESS, "Origin": "https://evil.example"},
            "ORIGIN_FORBIDDEN",
        ),
        (
            {"Host": FACTORY_ADDRESS, "Sec-Fetch-Site": "cross-site"},
            "CROSS_SITE_FORBIDDEN",
        ),
    ],
)
def test_external_host_origin_and_cross_site_fetch_are_rejected(
    headers: dict[str, str], error: str
) -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    response = application.handle("GET", "/api/v1/status", headers, "10.42.0.20")

    assert response.status == HTTPStatus.FORBIDDEN
    assert json.loads(response.body)["error"] == error


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "PROPFIND"])
def test_unsupported_method_or_route_is_rejected(method: str) -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    response = application.handle(method, "/api/v1/status", _headers(), "10.42.0.20")

    assert response.status == HTTPStatus.METHOD_NOT_ALLOWED
    assert json.loads(response.body)["error"] == "METHOD_NOT_ALLOWED"


def test_exact_action_envelope_is_forwarded_to_the_root_executor() -> None:
    client = FakeActionClient()
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=client,
        seal_portal=FakeSealPortal(),
    )
    request = {
        "operation": "CAPTURE_EMPTY_WEIGHT",
        "expectedRevision": 3,
        "parameters": {"confirmScaleEmpty": True},
    }
    body = json.dumps(request).encode("utf-8")

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.OK
    assert client.requests == [request]
    assert json.loads(response.body)["revision"] == 4


@pytest.mark.parametrize(
    ("overrides", "expected_status", "expected_error"),
    (
        ({"Origin": ""}, HTTPStatus.FORBIDDEN, "ORIGIN_FORBIDDEN"),
        (
            {"X-EcoBin-Factory-Action": "0"},
            HTTPStatus.FORBIDDEN,
            "ACTION_CSRF_GUARD_REQUIRED",
        ),
        (
            {"Content-Type": "text/plain"},
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            "JSON_CONTENT_TYPE_REQUIRED",
        ),
    ),
)
def test_actions_require_same_origin_custom_header_and_json_content_type(
    overrides: dict[str, str],
    expected_status: HTTPStatus,
    expected_error: str,
) -> None:
    body = b'{"operation":"CHECK_MCU","expectedRevision":1,"parameters":{}}'
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(), action_client=FakeActionClient()
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body, **overrides),
        "10.42.0.20",
        body,
    )

    assert response.status == expected_status
    assert json.loads(response.body)["error"] == expected_error


@pytest.mark.parametrize(
    "body",
    (
        b'[{"operation":"CHECK_MCU"}]',
        b'{"operation":"CHECK_MCU","operation":"RUN_CLEAN"}',
        b'{broken',
    ),
)
def test_action_json_is_an_object_without_duplicate_keys(body: bytes) -> None:
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(), action_client=FakeActionClient()
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert json.loads(response.body)["error"] == "ACTION_JSON_INVALID"


def test_executor_busy_error_is_preserved_at_the_http_boundary() -> None:
    client = FakeActionClient()
    client.error = AcceptancePortalClientError(
        "ACCEPTANCE_EXECUTOR_BUSY", HTTPStatus.CONFLICT
    )
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=client,
        seal_portal=FakeSealPortal(),
    )
    body = b'{"operation":"CHECK_MCU","expectedRevision":1,"parameters":{}}'

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.CONFLICT
    assert json.loads(response.body)["error"] == "ACCEPTANCE_EXECUTOR_BUSY"


def test_request_body_and_transfer_encoding_are_bounded() -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    body = application.handle(
        "GET", "/api/v1/status", _headers(**{"Content-Length": "1"}), "10.42.0.20"
    )
    too_large = application.handle(
        "GET", "/api/v1/status", _headers(**{"Content-Length": "4097"}), "10.42.0.20"
    )
    chunked = application.handle(
        "GET",
        "/api/v1/status",
        _headers(**{"Transfer-Encoding": "chunked"}),
        "10.42.0.20",
    )

    assert body.status == HTTPStatus.BAD_REQUEST
    assert too_large.status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert chunked.status == HTTPStatus.BAD_REQUEST


def test_query_strings_cannot_select_files_or_parameters() -> None:
    application = PortalApplication(snapshot_provider=FixedSnapshot())

    response = application.handle(
        "GET",
        "/api/v1/status?path=/etc/ecobin/setup-ap.key",
        _headers(),
        "10.42.0.20",
    )

    assert response.status == HTTPStatus.BAD_REQUEST
    assert b"setup-ap.key" not in response.body


def test_camera_images_are_available_only_for_the_current_review_nonce(
    tmp_path: Path,
) -> None:
    nonce = "a" * 32
    current = FakeActionClient(
        {
            "status": "RUNNING",
            "revision": 4,
            "cameraReview": {"nonce": nonce},
        }
    )
    (tmp_path / f"outside-{nonce}.jpg").write_bytes(b"outside-current")
    (tmp_path / f"inside-{nonce}.jpg").write_bytes(b"inside-current")
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=current,
        camera_review_directory=tmp_path,
    )

    outside = application.handle(
        "GET",
        f"/api/v1/acceptance/camera/outside/{nonce}",
        _headers(),
        "10.42.0.20",
    )
    stale = application.handle(
        "GET",
        f"/api/v1/acceptance/camera/outside/{'b' * 32}",
        _headers(),
        "10.42.0.20",
    )
    traversal = application.handle(
        "GET",
        f"/api/v1/acceptance/camera/outside/{nonce}?path=../../etc/shadow",
        _headers(),
        "10.42.0.20",
    )

    assert outside.status == HTTPStatus.OK
    assert outside.content_type == "image/jpeg"
    assert outside.body == b"outside-current"
    assert stale.status == HTTPStatus.NOT_FOUND
    assert traversal.status == HTTPStatus.BAD_REQUEST


def test_factory_seal_needs_current_pass_revision_and_server_authorization() -> None:
    uid = "12345678-1234-4123-8123-123456789abc"
    body = json.dumps(
        {
            "operation": "CONFIRM_FACTORY_SEAL",
            "expectedRevision": 12,
            "parameters": {"operatorConfirmationUid": uid},
        }
    ).encode("utf-8")
    accepted = FakeActionClient({"status": "PASSED", "revision": 12})
    seal = FakeSealPortal(allowed=True)
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=accepted,
        seal_portal=seal,
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.OK
    assert seal.confirmed == [uid]
    assert (
        json.loads(response.body)["factorySeal"]["statusCode"]
        == "SEALED_RESPONSE_PENDING"
    )


def test_factory_seal_presentation_ack_bypasses_the_terminal_action_gate() -> None:
    uid = "12345678-1234-4123-8123-123456789abc"
    body = json.dumps(
        {
            "operation": "ACK_FACTORY_SEAL_PRESENTED",
            "parameters": {"operatorConfirmationUid": uid},
        }
    ).encode("utf-8")
    seal = FakeSealPortal(allowed=True)
    seal.status_code = "SEALED_RESPONSE_PENDING"
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=FakeActionClient(),
        seal_portal=seal,
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.OK
    assert seal.acknowledged == [uid]


def test_hardware_actions_are_blocked_after_seal_is_committed() -> None:
    request = {
        "operation": "CHECK_MCU",
        "expectedRevision": 3,
        "parameters": {},
    }
    body = json.dumps(request).encode("utf-8")
    client = FakeActionClient()
    seal = FakeSealPortal(allowed=True)
    seal.status_code = "SEALED_RESPONSE_PENDING"
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=client,
        seal_portal=seal,
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.LOCKED
    assert json.loads(response.body)["error"] == (
        "FACTORY_SEAL_ALREADY_COMMITTED"
    )
    assert client.requests == []


def test_hardware_actions_fail_closed_when_seal_status_is_unavailable() -> None:
    body = json.dumps(
        {
            "operation": "CHECK_MCU",
            "expectedRevision": 3,
            "parameters": {},
        }
    ).encode("utf-8")
    client = FakeActionClient()
    seal = FakeSealPortal()
    seal.error = AcceptancePortalClientError(
        "FACTORY_SEAL_NOT_AVAILABLE",
        HTTPStatus.SERVICE_UNAVAILABLE,
    )
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=client,
        seal_portal=seal,
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.SERVICE_UNAVAILABLE
    assert json.loads(response.body)["error"] == "FACTORY_SEAL_NOT_AVAILABLE"
    assert client.requests == []


def test_hardware_actions_fail_closed_for_an_unrecognized_seal_status() -> None:
    body = json.dumps(
        {
            "operation": "CHECK_MCU",
            "expectedRevision": 3,
            "parameters": {},
        }
    ).encode("utf-8")
    client = FakeActionClient()
    seal = FakeSealPortal()
    seal.status_code = "FUTURE_SEAL_STATE"
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(),
        action_client=client,
        seal_portal=seal,
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status == HTTPStatus.LOCKED
    assert json.loads(response.body)["error"] == (
        "FACTORY_SEAL_ALREADY_COMMITTED"
    )
    assert client.requests == []


def test_seal_adapter_reports_status_boundary_failures_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import factory_seal.portal_client as portal_client

    def fail_status() -> dict[str, object]:
        raise FactorySealPortalError("FACTORY_SEAL_RESPONSE_INVALID")

    monkeypatch.setattr(
        portal_client,
        "get_factory_seal_authorization_status",
        fail_status,
    )

    with pytest.raises(AcceptancePortalClientError) as captured:
        FactorySealPortalAdapter().status()

    assert captured.value.code == "FACTORY_SEAL_RESPONSE_INVALID"
    assert captured.value.status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    ("status", "revision", "seal_allowed", "error"),
    (
        ("RUNNING", 12, True, "LOCAL_ACCEPTANCE_NOT_PASSED"),
        ("PASSED", 13, True, "ACCEPTANCE_REVISION_CONFLICT"),
        ("PASSED", 12, False, "CLOUD_ACCEPTANCE_REQUIRED"),
    ),
)
def test_factory_seal_fails_closed_before_any_confirmation(
    status: str,
    revision: int,
    seal_allowed: bool,
    error: str,
) -> None:
    body = json.dumps(
        {
            "operation": "CONFIRM_FACTORY_SEAL",
            "expectedRevision": 12,
            "parameters": {
                "operatorConfirmationUid": (
                    "12345678-1234-4123-8123-123456789abc"
                )
            },
        }
    ).encode("utf-8")
    client = FakeActionClient({"status": status, "revision": revision})
    seal = FakeSealPortal(allowed=seal_allowed)
    application = PortalApplication(
        snapshot_provider=FixedSnapshot(), action_client=client, seal_portal=seal
    )

    response = application.handle(
        "POST",
        "/api/v1/acceptance/action",
        _action_headers(body),
        "10.42.0.20",
        body,
    )

    assert response.status in {HTTPStatus.CONFLICT, HTTPStatus.LOCKED}
    assert json.loads(response.body)["error"] == error
    assert seal.confirmed == []


def test_public_projection_never_copies_secret_shaped_fields(tmp_path: Path) -> None:
    release = tmp_path / "release.json"
    public_status = tmp_path / "public-status.json"
    machine_id = tmp_path / "machine-id"
    disk_root = tmp_path / "data"
    acceptance_status = tmp_path / "acceptance.json"
    disk_root.mkdir()
    release.write_text(
        json.dumps(
            {
                "imageReleaseId": "image-2026.08.22",
                "version": "1.0.0",
                "enrollmentKey": "K1-SHOULD-NEVER-LEAK",
                "setupApPassword": "AP-SHOULD-NEVER-LEAK",
            }
        ),
        encoding="utf-8",
    )
    public_status.write_text(
        json.dumps(
            {
                "stage": "FACTORY_PORTAL_READY",
                "lastErrorCode": "NONE",
                "timeTrusted": False,
                "factoryTestStatus": "NOT_RUN",
                "cellular": {
                    "resultCode": "CELLULAR_DNS_UNAVAILABLE",
                    "consecutiveFailureCount": 3,
                    "retryScheduled": True,
                    "retryInSeconds": 9,
                },
                "deviceKey": "ONENET-SHOULD-NEVER-LEAK",
                "credentials": "CREDENTIAL-SHOULD-NEVER-LEAK",
            }
        ),
        encoding="utf-8",
    )
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="ascii")
    acceptance_status.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "NOT_RUN",
                "phase": "NOT_RUN",
                "revision": 0,
                "checks": {},
                "allowedActions": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = PortalSnapshotProvider(
        SnapshotPaths(
            image_release=release,
            public_status=public_status,
            machine_id=machine_id,
            disk_root=disk_root,
            acceptance_status=acceptance_status,
        )
    ).snapshot()
    encoded = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["stage"] == "FACTORY_PORTAL_READY"
    assert snapshot["readOnly"] is False
    assert snapshot["factoryTest"]["status"] == "NOT_RUN"
    assert snapshot["network"]["clientWanForwarding"] is False
    assert snapshot["network"]["cellular"] == {
        "resultCode": "CELLULAR_DNS_UNAVAILABLE",
        "consecutiveFailureCount": 3,
        "retryScheduled": True,
        "retryInSeconds": 9,
    }
    assert "SHOULD-NEVER-LEAK" not in encoded
    assert "deviceKey" not in encoded
    assert "credentials" not in encoded
    assert "setupApPassword" not in encoded


def test_static_assets_are_self_contained_and_make_no_external_requests() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (web / "index.html", web / "app.css", web / "app.js")
    )

    assert "https://" not in combined
    assert "http://" not in combined
    assert "cdn" not in combined.lower()
    assert "analytics" not in combined.lower()
    assert "<style" not in combined.lower()
    assert "<script>" not in combined.lower()


def test_web_surfaces_time_trust_and_the_precise_uplink_result() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="time-trusted"' in index
    assert 'id="last-error"' in index
    assert 'byId("time-trusted")' in app
    assert 'byId("last-error")' in app
    assert "status.system?.timeTrusted" in app
    assert "status.lastErrorCode" in app
    assert 'id="network-retry-panel"' in index
    assert 'id="network-retry-detail"' in index
    assert "status.network?.cellular" in app
    assert "consecutiveFailureCount" in app
    assert "retryInSeconds" in app
    for code in (
        "TIME_SYNC_PENDING",
        "TIME_TRUST_QUERY_FAILED",
        "CHRONY_ONLINE_FAILED",
        "CHRONY_ACTIVITY_FAILED",
        "CHRONY_SOURCES_UNAVAILABLE",
        "CHRONY_REFRESH_FAILED",
        "CHRONY_BURST_FAILED",
        "CHRONY_WAITSYNC_FAILED",
        "TIME_SYNC_INTERNAL_ERROR",
    ):
        assert code in app


def test_web_surfaces_each_runtime_service_without_unit_names() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="runtime-services-panel"' in index
    assert 'id="runtime-services-list"' in index
    for label in (
        "云端通信服务",
        "设备更新服务",
        "业务程序切换授权",
        "控制板升级授权",
        "设备管理启动自检",
        "设备日常业务服务",
        "蜂窝联网服务",
        "远程维护服务",
    ):
        assert label in app
    for internal_name in (
        "ecobin-communication.service",
        "ecobin-updater.service",
        "ecobin-business-activation-helper.socket",
        "ecobin-mcu-flash-helper.socket",
    ):
        assert internal_name not in index
        assert internal_name not in app


def test_camera_review_labels_follow_roles_instead_of_one_camera_model() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")

    assert "箱外摄像头" in index
    assert "箱内摄像头" in index
    assert "DECXIN" not in index
    assert "icspring" not in index


def test_web_exposes_a_distinct_post_delivery_safety_confirmation() -> None:
    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert "CONFIRM_DELIVERY_AREA_SAFE" in app
    assert "动作结束后重新检查现场" in app
    assert "operatorAreaSafeConfirmed: true" in app


def test_clean_recovery_requires_a_specific_physical_door_confirmation() -> None:
    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert 'cleanRecovery: operation === "RECOVER"' in app
    assert "清运门没有门位传感器" in app
    assert "请现场观察并确认清运门已经完全关闭" in app
    assert "cleanDoorClosedConfirmed: cleanRecovery" in app


def test_factory_seal_confirmation_does_not_require_a_secure_http_context() -> None:
    """The isolated factory AP is plain HTTP, so randomUUID is unavailable."""

    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert "crypto.randomUUID()" not in app
    assert "crypto.getRandomValues" in app
    assert "pendingSealUid ||= createUuidV4();" in app
    assert "BROWSER_RANDOM_UNAVAILABLE" in app


def test_factory_web_marks_and_announces_progress_only_when_it_changes() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="progress-announcement"' in index
    assert 'aria-live="polite"' in index
    assert 'aria-atomic="true"' in index
    assert '" is-current"' in app
    assert "lastProgressAnnouncement" in app
    assert "announcementSignature !== lastProgressAnnouncement" in app
    assert "setTextIfChanged" in app


def test_factory_seal_terminal_is_a_focus_isolating_modal() -> None:
    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "app.css").read_text(encoding="utf-8")

    assert 'id="factory-workspace"' in index
    assert 'role="dialog"' in index
    assert 'aria-modal="true"' in index
    assert 'aria-describedby="terminal-description"' in index
    assert 'document.querySelector(".topbar").inert = true;' in app
    assert 'byId("factory-workspace").inert = true;' in app
    assert 'event.key !== "Tab"' in app
    assert "event.preventDefault();" in app
    assert "body.is-terminal" in css
    assert ".sr-only" in css


def test_factory_web_bounds_requests_and_recovers_a_lost_seal_response() -> None:
    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert "async function fetchWithTimeout" in app
    assert "REQUEST_TIMEOUT_MS.status" in app
    assert "REQUEST_TIMEOUT_MS.action" in app
    assert "REQUEST_TIMEOUT_MS.seal" in app
    assert 'throw new Error("REQUEST_TIMEOUT")' in app
    assert "pendingSealUid ||= createUuidV4();" in app
    assert 'parameters: { operatorConfirmationUid: pendingSealUid }' in app
    assert "void refreshStatus();" in app


def test_factory_web_describes_runtime_transport_progress_codes() -> None:
    app = (
        Path(__file__).parents[1] / "factory" / "web" / "app.js"
    ).read_text(encoding="utf-8")

    for code in (
        "DEVICE_ENTRY_URL",
        "ONENET_TRANSPORT_ACCEPTED",
        "WAITING_ONENET_ACCEPTANCE",
        "WAITING_BACKEND_CONFIRMATION",
        "RUNTIME_SERVICE_STOPPED",
        "UART_FAILED",
        "UART_DISCONNECTED",
        "MQTT_FAILED",
    ):
        assert code in app


def test_factory_web_uses_operator_language_for_the_visible_flow() -> None:
    """Machine codes stay in the contract, but the operator flow must translate them."""

    web = Path(__file__).parents[1] / "factory" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    app = (web / "app.js").read_text(encoding="utf-8")

    assert "P7" not in index
    assert "P8" not in index
    assert re.search(r"P[78](?![A-Z0-9_])", app) is None
    assert 'status.stage || "UNKNOWN"' not in app
    assert 'status.factoryTest?.status || "NOT_RUN"' not in app
    assert 'status.factorySeal?.statusCode || "UNKNOWN"' not in app
    assert 'factory.phase || "待操作"' not in app
    assert '${recovery.context || "UNKNOWN"}' not in app
    assert "STEP_LABELS[step.id] || step.id" not in app
    assert 'byId("diagnostics").open = true' not in app
    assert "错误码 ${problem.code}" not in app
    assert 'id="last-error-code"' in index
    assert 'id="flow-error-code"' in index
    assert "FIRST_BOOT_STAGE_LABELS" in app
    assert "FACTORY_TEST_STATUS_LABELS" in app
    assert "FACTORY_SEAL_STATUS_LABELS" in app
    assert "FACTORY_PHASE_LABELS" in app
    assert 'EVIDENCE_CONFIRMED: "后台已确认验收信息"' in app
    assert "actionErrorDescription(error.message)" in app
    assert 'releaseId === "UNKNOWN"' in app
    assert 'technicalCodeText(problem?.code)' in app
    assert 'step.state === "BLOCKED"' in app
    assert re.search(
        r'step\.errorCode\s*&&\s*step\.errorCode !== "NONE"',
        app,
    )
    describe_error = re.search(
        r"function describeError\(node\) \{(.*?)\n\}",
        app,
        re.DOTALL,
    )
    assert describe_error is not None
    assert "blockedStepProblem(node)" in describe_error.group(1)
    assert "factoryRecoveryProblem(node)" in describe_error.group(1)
    assert "factoryTest?.recovery" in app
    assert "factoryTest.recovery.resultCode" in app
    assert (
        'LEGACY_ACTION_SAFETY_FAILURE_READY: "旧检查记录缺少现场安全确认，'
        '请先保存本轮失败报告"'
    ) in app
    assert 'code === "CELLULAR_PROFILE_INSTALL_FAILED"' in app
    assert 'code === "CELLULAR_ACTIVATION_FAILED"' in app
    assert "存储空间、文件系统和权限" in app
    assert "联网模式或驱动与当前镜像不匹配" in app

    def object_keys(name: str) -> set[str]:
        match = re.search(
            rf"const {name} = Object\.freeze\(\{{(.*?)\}}\);",
            app,
            re.DOTALL,
        )
        assert match is not None, name
        return set(re.findall(r"^\s{2}([A-Z0-9_]+):", match.group(1), re.MULTILINE))

    seal_labels = object_keys("FACTORY_SEAL_STATUS_LABELS")
    assert _SEAL_STATUS_CODES <= seal_labels
    assert {stage.value for stage in FirstBootStage} <= object_keys(
        "FIRST_BOOT_STAGE_LABELS"
    )
    assert {status.value for status in FactoryTestStatus} <= object_keys(
        "FACTORY_TEST_STATUS_LABELS"
    )

    error_labels = object_keys("ERROR_DESCRIPTIONS")
    assert {
        "FACTORY_TEST_FAILED",
        "FACTORY_STATE_INVALID",
        "FACTORY_RECOVERY_REQUIRED",
        "FACTORY_REPORT_INVALID",
        "IMAGE_RELEASE_INVALID",
        "FACTORY_SEAL_LOCAL_FACT_CHANGED",
        "ENROLLMENT_CLEANUP_REQUIRED",
        "DEVICE_CREDENTIALS_INVALID",
        "DEVICE_CAPABILITIES_INVALID",
        "MAINTENANCE_BUSY",
        "SYSTEM_FACTS_INVALID",
        "FACTORY_TEST_GATE_CLOSED",
        "TIME_NOT_TRUSTED",
        "MCU_RESET_LINE_REQUIRED_FOR_RECOVERY",
        "APPLICATION_RECOVERY_FAILED",
    } <= error_labels
    assert {
        "CELLULAR_CONFIG_MISSING",
        "CELLULAR_CONFIG_NOT_REGULAR",
        "CELLULAR_CONFIG_PERMISSIONS",
        "CELLULAR_CONFIG_SIZE",
        "CELLULAR_CONFIG_ENCODING",
        "CELLULAR_CONFIG_SYNTAX",
        "CELLULAR_CONFIG_FIELDS",
        "CELLULAR_CONFIG_VALUE",
        "CELLULAR_CONFIG_SCHEMA",
        "CELLULAR_HIL_LOCKED",
        "CELLULAR_APN_MODE_UNSUPPORTED",
        "CELLULAR_CONNECTION_ID_INVALID",
        "CELLULAR_USB_DRIVER_UNKNOWN",
        "CELLULAR_USB_PROFILE_UNKNOWN",
        "CELLULAR_PROBE_IPV4_INVALID",
        "CELLULAR_HTTPS_PROBE_INVALID",
        "CELLULAR_RNDIS_AMBIGUOUS",
        "CELLULAR_USB_PARENT_UNVERIFIED",
        "CELLULAR_USB_DRIVER_MISMATCH",
        "CELLULAR_RNDIS_UNAVAILABLE",
        "CELLULAR_INTERFACE_INVALID",
        "CELLULAR_SIM_ABSENT",
        "CELLULAR_SIM_LOCKED",
        "CELLULAR_DHCP_UNAVAILABLE",
        "CELLULAR_DEFAULT_ROUTE_WRONG_INTERFACE",
        "CELLULAR_DNS_UNAVAILABLE",
        "CELLULAR_HTTPS_UNAVAILABLE",
        "CELLULAR_PROFILE_INSTALL_FAILED",
        "CELLULAR_ACTIVATION_FAILED",
        "CELLULAR_EGRESS_GATE_FAILED",
    } <= error_labels

    phase_labels = object_keys("FACTORY_PHASE_LABELS")
    assert {
        "DELIVERY_SAFE_VERIFIED",
        "LEGACY_ACTION_SAFETY_FAILURE_READY",
    } <= phase_labels


def test_server_builder_uses_only_the_fixed_ap_address() -> None:
    captured: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address: tuple[str, int], handler: object) -> None:
            captured["address"] = address
            captured["handler"] = handler

    server = build_server(
        PortalApplication(snapshot_provider=FixedSnapshot()),
        server_factory=FakeServer,
    )

    assert isinstance(server, FakeServer)
    assert captured["address"] == ("10.42.0.1", 80)
    with pytest.raises(ValueError, match="only bind"):
        FactoryPortalServer(("0.0.0.0", FACTORY_PORT), captured["handler"])


def test_http_security_headers_block_embedding_and_external_content() -> None:
    headers = security_headers()

    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "connect-src 'self'" in headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]


def test_header_reader_accepts_a_small_complete_request() -> None:
    reader = BoundedHeaderReader(
        io.BytesIO(b"GET /healthz HTTP/1.1\r\nHost: 10.42.0.1\r\n\r\n")
    )

    assert reader.readline() == b"GET /healthz HTTP/1.1\r\n"
    assert reader.readline() == b"Host: 10.42.0.1\r\n"
    assert reader.readline() == b"\r\n"


def test_header_reader_rejects_an_oversized_request_line() -> None:
    reader = BoundedHeaderReader(io.BytesIO(b"G" * (MAX_REQUEST_LINE_BYTES + 1)))

    with pytest.raises(RequestHeaderLimitExceeded, match="line"):
        reader.readline()


def test_header_reader_rejects_an_oversized_single_header() -> None:
    reader = BoundedHeaderReader(
        io.BytesIO(
            b"GET / HTTP/1.1\r\n"
            + b"X-Fill: "
            + b"a" * MAX_HEADER_LINE_BYTES
            + b"\r\n"
        )
    )
    reader.readline()

    with pytest.raises(RequestHeaderLimitExceeded, match="line"):
        reader.readline()


def test_header_reader_rejects_too_many_headers() -> None:
    raw = b"GET / HTTP/1.1\r\n" + b"X: a\r\n" * (MAX_HEADER_COUNT + 1)
    reader = BoundedHeaderReader(io.BytesIO(raw))
    reader.readline()

    for _ in range(MAX_HEADER_COUNT):
        reader.readline()
    with pytest.raises(RequestHeaderLimitExceeded, match="count"):
        reader.readline()


def test_header_reader_rejects_an_oversized_aggregate_header_block() -> None:
    line = b"X-Fill: " + b"a" * (MAX_HEADER_LINE_BYTES - 10) + b"\r\n"
    assert len(line) <= MAX_HEADER_LINE_BYTES
    assert len(line) * 3 > MAX_HEADER_BYTES
    reader = BoundedHeaderReader(io.BytesIO(b"GET / HTTP/1.1\r\n" + line * 3))
    reader.readline()
    reader.readline()
    reader.readline()

    with pytest.raises(RequestHeaderLimitExceeded, match="block"):
        reader.readline()
