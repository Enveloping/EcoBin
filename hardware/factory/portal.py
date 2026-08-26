"""Low-privilege local portal and narrow P7 action proxy."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import threading
import time
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .acceptance_portal_client import (
    AcceptancePortalClient,
    AcceptancePortalClientError,
)
from .network import FACTORY_ADDRESS, FACTORY_PORT


MAX_REQUEST_BODY_BYTES = 4096
MAX_STATUS_FILE_BYTES = 64 * 1024
MAX_REQUESTS_PER_MINUTE = 120
MAX_CONCURRENT_REQUESTS = 16
CLIENT_SOCKET_TIMEOUT_SECONDS = 5.0
MAX_REQUEST_LINE_BYTES = 4096
MAX_HEADER_LINE_BYTES = 8192
MAX_HEADER_BYTES = 16 * 1024
MAX_HEADER_COUNT = 64
WEB_DIRECTORY = Path(__file__).with_name("web")
CAMERA_REVIEW_DIRECTORY = Path("/run/ecobin/factory-test/photos")

_SAFE_CODE = re.compile(r"^[A-Z0-9_.:-]{1,64}$")
_SAFE_RELEASE = re.compile(r"^[A-Za-z0-9_.:+-]{1,128}$")
_REPORT_STATUSES = {
    "NOT_RUN",
    "RUNNING",
    "PASSED",
    "FAILED",
    "RECOVERY_REQUIRED",
}
_CAMERA_IMAGE_PATH = re.compile(
    r"^/api/v1/acceptance/camera/(outside|inside)/([0-9a-f]{32})$"
)
_UUID_V4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class RequestHeaderLimitExceeded(ValueError):
    """Raised before HTTP parsing can allocate unbounded header data."""


class BoundedHeaderReader:
    """Bound the request line and complete header block for one-shot HTTP/1.1.

    The server closes every connection after one response, so this wrapper only
    needs one request-header phase.  Body reads are delegated unchanged; all
    accepted routes reject request bodies separately.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._request_line_seen = False
        self._headers_complete = False
        self._header_bytes = 0
        self._header_count = 0

    def readline(self, size: int = -1) -> bytes:
        if self._headers_complete:
            return self._stream.readline(size)

        limit = (
            MAX_HEADER_LINE_BYTES
            if self._request_line_seen
            else MAX_REQUEST_LINE_BYTES
        )
        bounded_size = limit + 1 if size < 0 or size > limit + 1 else size
        line = self._stream.readline(bounded_size)
        if len(line) > limit:
            raise RequestHeaderLimitExceeded("HTTP line exceeds fixed limit")

        if not self._request_line_seen:
            self._request_line_seen = True
            return line

        self._header_bytes += len(line)
        if self._header_bytes > MAX_HEADER_BYTES:
            raise RequestHeaderLimitExceeded("HTTP header block exceeds fixed limit")
        if line in {b"\n", b"\r\n", b""}:
            self._headers_complete = True
            return line
        self._header_count += 1
        if self._header_count > MAX_HEADER_COUNT:
            raise RequestHeaderLimitExceeded("HTTP header count exceeds fixed limit")
        return line

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@dataclass(frozen=True, slots=True)
class SnapshotPaths:
    image_release: Path = Path("/etc/ecobin/image-release.json")
    public_status: Path = Path("/run/ecobin/factory-portal/status.json")
    machine_id: Path = Path("/etc/machine-id")
    disk_root: Path = Path("/")
    acceptance_status: Path = Path(
        "/run/ecobin/factory-portal/acceptance.json"
    )
    camera_review_directory: Path = CAMERA_REVIEW_DIRECTORY


@dataclass(frozen=True, slots=True)
class PortalResponse:
    status: HTTPStatus
    content_type: str
    body: bytes


class SnapshotProvider(Protocol):
    def snapshot(self) -> dict[str, Any]: ...


class ActionClient(Protocol):
    def status(self) -> dict[str, Any]: ...
    def execute(self, request: dict[str, Any]) -> dict[str, Any]: ...


class SealPortal(Protocol):
    def status(self) -> dict[str, Any]: ...
    def confirm(self, operator_confirmation_uid: str) -> dict[str, Any]: ...


def _read_regular_file(path: Path, maximum_bytes: int) -> bytes | None:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > maximum_bytes:
            return None
        content = os.read(descriptor, maximum_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > maximum_bytes:
        return None
    return content


def _read_json_object(path: Path) -> dict[str, Any]:
    content = _read_regular_file(path, MAX_STATUS_FILE_BYTES)
    if content is None:
        return {}
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _safe_string(value: Any, pattern: re.Pattern[str], fallback: str) -> str:
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return fallback


class FactorySealPortalAdapter:
    """Optional P8 client; absence never creates a local seal substitute."""

    @staticmethod
    def _validate(value: object) -> dict[str, Any]:
        exact = {
            "authorized",
            "confirmAllowed",
            "statusCode",
            "acceptanceGeneration",
            "authorizationBindingSha256",
        }
        if not isinstance(value, dict) or set(value) != exact:
            raise RuntimeError("factory seal projection is invalid")
        code = value.get("statusCode")
        binding = value.get("authorizationBindingSha256")
        generation = value.get("acceptanceGeneration")
        if (
            not isinstance(value.get("authorized"), bool)
            or not isinstance(value.get("confirmAllowed"), bool)
            or not isinstance(code, str)
            or _SAFE_CODE.fullmatch(code) is None
            or not (
                generation is None
                or (
                    isinstance(generation, int)
                    and not isinstance(generation, bool)
                    and generation >= 0
                )
            )
            or not (
                binding is None
                or (
                    isinstance(binding, str)
                    and re.fullmatch(r"[0-9a-f]{64}", binding)
                )
            )
        ):
            raise RuntimeError("factory seal projection is invalid")
        return dict(value)

    @staticmethod
    def _unavailable(code: str = "FACTORY_SEAL_NOT_AVAILABLE") -> dict[str, Any]:
        return {
            "authorized": False,
            "confirmAllowed": False,
            "statusCode": code,
            "acceptanceGeneration": None,
            "authorizationBindingSha256": None,
        }

    def status(self) -> dict[str, Any]:
        try:
            from factory_seal.portal_client import (
                FactorySealPortalError,
                get_factory_seal_authorization_status,
            )
        except ImportError:
            return self._unavailable()
        try:
            return self._validate(get_factory_seal_authorization_status())
        except (FactorySealPortalError, RuntimeError) as error:
            return self._unavailable(
                _safe_string(
                    getattr(error, "code", None),
                    _SAFE_CODE,
                    "FACTORY_SEAL_NOT_AVAILABLE",
                )
            )

    def confirm(self, operator_confirmation_uid: str) -> dict[str, Any]:
        try:
            from factory_seal.portal_client import (
                FactorySealPortalError,
                confirm_factory_seal,
            )
        except ImportError as error:
            raise AcceptancePortalClientError(
                "FACTORY_SEAL_NOT_AVAILABLE", HTTPStatus.SERVICE_UNAVAILABLE
            ) from error
        try:
            return self._validate(
                confirm_factory_seal(operator_confirmation_uid)
            )
        except (FactorySealPortalError, RuntimeError) as error:
            raise AcceptancePortalClientError(
                _safe_string(
                    getattr(error, "code", None),
                    _SAFE_CODE,
                    "FACTORY_SEAL_NOT_AVAILABLE",
                ),
                HTTPStatus.CONFLICT,
            ) from error


class PortalSnapshotProvider:
    """Build a public projection from an explicit, fixed file allowlist."""

    def __init__(
        self,
        paths: SnapshotPaths | None = None,
        *,
        acceptance_client: ActionClient | None = None,
        seal_portal: SealPortal | None = None,
    ) -> None:
        self._paths = paths or SnapshotPaths()
        self._acceptance_client = acceptance_client or AcceptancePortalClient()
        self._seal_portal = seal_portal or FactorySealPortalAdapter()

    def snapshot(self) -> dict[str, Any]:
        release = _read_json_object(self._paths.image_release)
        public_status = _read_json_object(self._paths.public_status)

        release_id = _safe_string(
            release.get("releaseId", release.get("imageReleaseId")),
            _SAFE_RELEASE,
            "UNKNOWN",
        )
        release_version = _safe_string(
            release.get("version"), _SAFE_RELEASE, "UNKNOWN"
        )
        stage = _safe_string(public_status.get("stage"), _SAFE_CODE, "UNKNOWN")
        error_code = _safe_string(
            public_status.get("lastErrorCode"), _SAFE_CODE, "NONE"
        )
        report_status = _safe_string(
            public_status.get("factoryTestStatus"), _SAFE_CODE, "NOT_RUN"
        )
        if report_status not in _REPORT_STATUSES:
            report_status = "NOT_RUN"

        machine_id = _read_regular_file(self._paths.machine_id, 128)
        machine_summary = "UNAVAILABLE"
        if machine_id:
            normalized = machine_id.decode("ascii", errors="ignore").strip().lower()
            if re.fullmatch(r"[0-9a-f]{32}", normalized):
                machine_summary = hashlib.sha256(
                    f"{normalized}\n{release_id}".encode("utf-8")
                ).hexdigest()[:12].upper()

        disk: dict[str, int | None] = {"totalBytes": None, "freeBytes": None}
        try:
            usage = shutil.disk_usage(self._paths.disk_root)
            disk = {"totalBytes": usage.total, "freeBytes": usage.free}
        except OSError:
            pass

        try:
            acceptance = self._acceptance_client.status()
        except AcceptancePortalClientError as error:
            acceptance = _read_json_object(self._paths.acceptance_status)
            if (
                acceptance.get("schemaVersion") != 1
                or acceptance.get("status") not in _REPORT_STATUSES
                or not isinstance(acceptance.get("revision"), int)
            ):
                acceptance = {
                    "executorAvailable": False,
                    "status": report_status,
                    "phase": "EXECUTOR_UNAVAILABLE",
                    "revision": 0,
                    "checks": {},
                    "allowedActions": [],
                    "cameraReview": None,
                    "lastErrorCode": error.code,
                }
            else:
                acceptance = dict(acceptance)
                acceptance["executorAvailable"] = False
        seal = self._seal_portal.status()
        if acceptance.get("status") != "PASSED":
            seal = dict(seal)
            seal["confirmAllowed"] = False

        checks = acceptance.get("checks")
        checks = checks if isinstance(checks, dict) else {}

        def capability_state(name: str) -> str:
            value = checks.get(name)
            if isinstance(value, dict):
                return _safe_string(
                    value.get("resultCode"), _SAFE_CODE, "NOT_RUN"
                )
            return "NOT_RUN"

        return {
            "schemaVersion": 1,
            "mode": "OFFLINE_FACTORY",
            "readOnly": False,
            "stage": stage,
            "lastErrorCode": error_code,
            "image": {
                "releaseId": release_id,
                "version": release_version,
                "board": "Orange Pi Zero 3 v1.2",
                "targetOs": "Debian 12 / Linux 6.1",
            },
            "system": {
                "machineSummary": machine_summary,
                "observedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "timeTrusted": bool(public_status.get("timeTrusted") is True),
                "disk": disk,
            },
            "network": {
                "portalAddress": f"http://{FACTORY_ADDRESS}/",
                "clientWanForwarding": False,
                "allowedLocalServices": ["DHCP", "DNS", "HTTP"],
            },
            "factoryTest": {
                "status": _safe_string(
                    acceptance.get("status"), _SAFE_CODE, report_status
                ),
                "phase": _safe_string(
                    acceptance.get("phase"), _SAFE_CODE, "UNKNOWN"
                ),
                "revision": (
                    acceptance.get("revision")
                    if isinstance(acceptance.get("revision"), int)
                    else 0
                ),
                "executorAvailable": acceptance.get("executorAvailable") is True,
                "allowedActions": acceptance.get("allowedActions", []),
                "checks": checks,
                "recovery": acceptance.get("recovery"),
                "cameraReview": acceptance.get("cameraReview"),
                "mcuIdentity": acceptance.get("mcuIdentity"),
                "mcuUpdateLineInstalled": acceptance.get(
                    "mcuUpdateLineInstalled"
                ),
                "mcuPeripheralEvidenceMode": acceptance.get(
                    "mcuPeripheralEvidenceMode"
                ),
                "hardwareConfigSummary": acceptance.get(
                    "hardwareConfigSummary"
                ),
            },
            "factorySeal": seal,
            "capabilities": [
                {
                    "id": "uart5-mcu",
                    "label": "UART5 / MCU revision 2",
                    "state": capability_state("mcu"),
                },
                {
                    "id": "dual-camera",
                    "label": "箱外 DECXIN / 箱内 icspring",
                    "state": capability_state("cameras"),
                },
                {
                    "id": "delivery-action",
                    "label": "离线投递硬件测试",
                    "state": capability_state("delivery"),
                },
                {
                    "id": "clean-action",
                    "label": "离线清运硬件测试",
                    "state": capability_state("clean"),
                },
            ],
            "hardwareInLoopGates": [
                "Zero 3 板载 Wi-Fi AP 冷启动稳定性",
                "热点客户端无法访问 SSH 或转发到 WAN",
                "手机在未插 SIM、蜂窝离线和蜂窝在线时均可访问本页",
            ],
        }


class RateLimiter:
    def __init__(
        self,
        maximum_requests: int = MAX_REQUESTS_PER_MINUTE,
        window_seconds: float = 60.0,
        clock: Any = time.monotonic,
    ) -> None:
        self._maximum = maximum_requests
        self._window = window_seconds
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client_address: str) -> bool:
        now = float(self._clock())
        cutoff = now - self._window
        with self._lock:
            requests = self._requests[client_address]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self._maximum:
                return False
            requests.append(now)
            return True


class PortalApplication:
    """Pure request router; it has no filesystem path or command parameters."""

    def __init__(
        self,
        snapshot_provider: SnapshotProvider | None = None,
        rate_limiter: RateLimiter | None = None,
        action_client: ActionClient | None = None,
        seal_portal: SealPortal | None = None,
        camera_review_directory: Path = CAMERA_REVIEW_DIRECTORY,
    ) -> None:
        self._action_client = action_client or AcceptancePortalClient()
        self._seal_portal = seal_portal or FactorySealPortalAdapter()
        self._snapshot_provider = snapshot_provider or PortalSnapshotProvider(
            acceptance_client=self._action_client,
            seal_portal=self._seal_portal,
        )
        self._rate_limiter = rate_limiter or RateLimiter()
        if not (
            camera_review_directory.is_absolute()
            or camera_review_directory.as_posix().startswith("/")
        ):
            raise ValueError("camera review directory must be absolute")
        self._camera_review_directory = camera_review_directory
        self._assets = {
            "/": ("text/html; charset=utf-8", self._load_asset("index.html")),
            "/assets/app.css": ("text/css; charset=utf-8", self._load_asset("app.css")),
            "/assets/app.js": (
                "text/javascript; charset=utf-8",
                self._load_asset("app.js"),
            ),
        }

    @staticmethod
    def _load_asset(filename: str) -> bytes:
        # filename is selected only from the three literals above, never from a
        # URL.  This is the portal's complete static-file allowlist.
        content = _read_regular_file(WEB_DIRECTORY / filename, 256 * 1024)
        if content is None:
            raise RuntimeError(f"factory portal asset is unavailable: {filename}")
        return content

    @staticmethod
    def _json(status: HTTPStatus, payload: Mapping[str, Any]) -> PortalResponse:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return PortalResponse(status, "application/json; charset=utf-8", body)

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        lowered = name.lower()
        for key, value in headers.items():
            if key.lower() == lowered:
                return value
        return None

    def handle(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str],
        client_address: str,
        body: bytes = b"",
    ) -> PortalResponse:
        transfer_encoding = self._header(headers, "Transfer-Encoding")
        if transfer_encoding:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "BODY_ENCODING_FORBIDDEN"})

        content_length = self._header(headers, "Content-Length")
        try:
            declared_length = int(content_length or "0")
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "INVALID_CONTENT_LENGTH"})
        if declared_length < 0 or declared_length > MAX_REQUEST_BODY_BYTES:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "BODY_TOO_LARGE"})
        if not isinstance(body, bytes) or len(body) != declared_length:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "REQUEST_BODY_INCOMPLETE"})

        if method not in {"GET", "HEAD", "POST"}:
            return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_NOT_ALLOWED"})

        host = self._header(headers, "Host")
        if host not in {FACTORY_ADDRESS, f"{FACTORY_ADDRESS}:{FACTORY_PORT}"}:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "HOST_FORBIDDEN"})
        origin = self._header(headers, "Origin")
        allowed_origins = {
            f"http://{FACTORY_ADDRESS}",
            f"http://{FACTORY_ADDRESS}:{FACTORY_PORT}",
        }
        if origin is not None and origin not in allowed_origins:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "ORIGIN_FORBIDDEN"})
        fetch_site = self._header(headers, "Sec-Fetch-Site")
        if fetch_site is not None and fetch_site not in {"none", "same-origin"}:
            return self._json(HTTPStatus.FORBIDDEN, {"error": "CROSS_SITE_FORBIDDEN"})

        if not self._rate_limiter.allow(client_address):
            return self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "RATE_LIMITED"})

        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "INVALID_TARGET"})
        if method in {"GET", "HEAD"} and declared_length != 0:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "REQUEST_BODY_FORBIDDEN"})
        if parsed.path == "/api/v1/status":
            if method == "POST":
                return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_NOT_ALLOWED"})
            return self._json(HTTPStatus.OK, self._snapshot_provider.snapshot())
        if parsed.path == "/healthz":
            if method == "POST":
                return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_NOT_ALLOWED"})
            return PortalResponse(HTTPStatus.OK, "text/plain; charset=utf-8", b"ok\n")
        camera_match = _CAMERA_IMAGE_PATH.fullmatch(parsed.path)
        if camera_match is not None:
            if method == "POST":
                return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_NOT_ALLOWED"})
            return self._camera_image(camera_match.group(1), camera_match.group(2))
        if parsed.path == "/api/v1/acceptance/action":
            if method != "POST":
                return self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "METHOD_NOT_ALLOWED"})
            return self._execute_action(headers, body)
        if method == "POST":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
        asset = self._assets.get(parsed.path)
        if asset is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})
        return PortalResponse(HTTPStatus.OK, asset[0], asset[1])

    @staticmethod
    def _strict_json_object(body: bytes) -> dict[str, Any]:
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        try:
            value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("invalid JSON request") from error
        if not isinstance(value, dict):
            raise ValueError("JSON request must be an object")
        return value

    def _execute_action(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> PortalResponse:
        content_type = self._header(headers, "Content-Type")
        origin = self._header(headers, "Origin")
        action_header = self._header(headers, "X-EcoBin-Factory-Action")
        if content_type != "application/json":
            return self._json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "JSON_CONTENT_TYPE_REQUIRED"},
            )
        if origin not in {
            f"http://{FACTORY_ADDRESS}",
            f"http://{FACTORY_ADDRESS}:{FACTORY_PORT}",
        } or action_header != "1":
            return self._json(
                HTTPStatus.FORBIDDEN,
                {"error": "ACTION_CSRF_GUARD_REQUIRED"},
            )
        try:
            request = self._strict_json_object(body)
        except ValueError:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "ACTION_JSON_INVALID"})
        if request.get("operation") == "CONFIRM_FACTORY_SEAL":
            return self._confirm_factory_seal(request)
        try:
            result = self._action_client.execute(request)
        except AcceptancePortalClientError as error:
            return self._json(error.status, {"error": error.code})
        return self._json(HTTPStatus.OK, result)

    def _confirm_factory_seal(self, request: dict[str, Any]) -> PortalResponse:
        if set(request) != {"operation", "expectedRevision", "parameters"}:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "ACTION_REQUEST_INVALID"})
        expected = request.get("expectedRevision")
        parameters = request.get("parameters")
        if (
            not isinstance(expected, int)
            or isinstance(expected, bool)
            or expected < 0
            or not isinstance(parameters, dict)
            or set(parameters) != {"operatorConfirmationUid"}
        ):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "ACTION_PARAMETERS_INVALID"})
        uid = parameters.get("operatorConfirmationUid")
        if not isinstance(uid, str) or _UUID_V4.fullmatch(uid) is None:
            return self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": "OPERATOR_CONFIRMATION_UID_INVALID"},
            )
        try:
            try:
                acceptance = self._action_client.status()
            except AcceptancePortalClientError:
                acceptance = self._snapshot_provider.snapshot().get(
                    "factoryTest", {}
                )
            if acceptance.get("revision") != expected:
                return self._json(
                    HTTPStatus.CONFLICT,
                    {"error": "ACCEPTANCE_REVISION_CONFLICT"},
                )
            if acceptance.get("status") != "PASSED":
                return self._json(
                    HTTPStatus.LOCKED,
                    {"error": "LOCAL_ACCEPTANCE_NOT_PASSED"},
                )
            seal = self._seal_portal.status()
            if seal.get("confirmAllowed") is not True:
                return self._json(
                    HTTPStatus.LOCKED,
                    {"error": _safe_string(seal.get("statusCode"), _SAFE_CODE, "FACTORY_SEAL_NOT_AUTHORIZED")},
                )
            result = self._seal_portal.confirm(uid)
        except AcceptancePortalClientError as error:
            return self._json(error.status, {"error": error.code})
        return self._json(HTTPStatus.OK, {"factorySeal": result})

    def _camera_image(self, role: str, nonce: str) -> PortalResponse:
        try:
            try:
                status = self._action_client.status()
            except AcceptancePortalClientError:
                status = self._snapshot_provider.snapshot().get(
                    "factoryTest", {}
                )
        except (AttributeError, TypeError):
            return self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "ACCEPTANCE_EXECUTOR_UNAVAILABLE"},
            )
        review = status.get("cameraReview") if isinstance(status, dict) else None
        if not isinstance(review, dict) or review.get("nonce") != nonce:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "CAMERA_REVIEW_NOT_FOUND"})
        filename = f"{role}-{nonce}.jpg"
        content = _read_regular_file(
            self._camera_review_directory / filename,
            2 * 1024 * 1024,
        )
        if content is None or not content:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "CAMERA_REVIEW_NOT_FOUND"})
        return PortalResponse(HTTPStatus.OK, "image/jpeg", content)


def security_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, max-age=0",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'none'"
        ),
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    }


def _handler_type(application: PortalApplication) -> type[BaseHTTPRequestHandler]:
    class FactoryPortalHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "EcoBinFactoryPortal/1"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self.rfile = BoundedHeaderReader(self.rfile)

        def handle_one_request(self) -> None:
            try:
                super().handle_one_request()
            except RequestHeaderLimitExceeded:
                # Parsing may not yet have established a safe HTTP version, so
                # close silently rather than reflecting attacker-controlled data.
                self.close_connection = True

        def _respond(self) -> None:
            headers = {key: value for key, value in self.headers.items()}
            body = b""
            try:
                declared = int(application._header(headers, "Content-Length") or "0")
            except ValueError:
                declared = 0
            if 0 < declared <= MAX_REQUEST_BODY_BYTES:
                body = self.rfile.read(declared)
            response = application.handle(
                self.command,
                self.path,
                headers,
                self.client_address[0],
                body,
            )
            self.send_response(response.status.value)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            for key, value in security_headers().items():
                self.send_header(key, value)
            if response.status == HTTPStatus.METHOD_NOT_ALLOWED:
                self.send_header("Allow", "GET, HEAD, POST")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)
            self.close_connection = True

        do_GET = _respond
        do_HEAD = _respond
        do_POST = _respond
        do_PUT = _respond
        do_PATCH = _respond
        do_DELETE = _respond
        do_OPTIONS = _respond
        do_CONNECT = _respond
        do_TRACE = _respond

        def __getattr__(self, name: str) -> Any:
            if name.startswith("do_"):
                return self._respond
            raise AttributeError(name)

        def version_string(self) -> str:
            return self.server_version

        def log_message(self, _format: str, *args: object) -> None:
            # Paths and query strings are omitted so access logs cannot become a
            # secret exfiltration channel.  systemd still records process health.
            return

    return FactoryPortalHandler


class FactoryPortalServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
    ) -> None:
        if server_address != (FACTORY_ADDRESS, FACTORY_PORT):
            raise ValueError("factory portal may only bind 10.42.0.1:80")
        self._request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
        super().__init__(server_address, handler_class)

    def get_request(self) -> tuple[socket.socket, Any]:
        request, address = super().get_request()
        request.settimeout(CLIENT_SOCKET_TIMEOUT_SECONDS)
        return request, address

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(
        self, request: socket.socket, client_address: Any
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class ServerFactory(Protocol):
    def __call__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
    ) -> Any: ...


def build_server(
    application: PortalApplication | None = None,
    *,
    server_factory: ServerFactory = FactoryPortalServer,
) -> Any:
    app = application or PortalApplication()
    return server_factory((FACTORY_ADDRESS, FACTORY_PORT), _handler_type(app))


def main() -> int:
    server = build_server()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
