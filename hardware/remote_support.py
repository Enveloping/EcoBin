"""On-demand reverse-SSH lifecycle independent from the physical work slot."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable

from device_credentials import RemoteSupportCredentials
from secure_files import atomic_write_bytes
from trusted_clock import local_deadline_reference


logger = logging.getLogger("remote-support")

REMOTE_PORTS = frozenset(range(22011, 22015))


class _BoundedOutputCollector:
    """Drain a child pipe without allowing unbounded memory or log growth."""

    def __init__(self, stream: BinaryIO | None, limit: int = 4096):
        self._stream = stream
        self._limit = limit
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        if stream is not None:
            self._thread = threading.Thread(
                target=self._read,
                daemon=True,
                name="remote-ssh-output",
            )
            self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self._stream.read(1024)
                if not chunk:
                    return
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                with self._lock:
                    self._buffer.extend(chunk)
                    overflow = len(self._buffer) - self._limit
                    if overflow > 0:
                        del self._buffer[:overflow]
        except (OSError, ValueError):
            return

    def snapshot(self) -> bytes:
        with self._lock:
            return bytes(self._buffer)

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.2)


class RemoteSupportManager:
    """Persist desired state and supervise exactly one OpenSSH child."""

    def __init__(
        self,
        store,
        credentials: RemoteSupportCredentials | None,
        *,
        runtime_dir: str | os.PathLike[str] = "/run/ecobin/remote-support",
        popen_factory: Callable[..., Any] = subprocess.Popen,
        utc_now: Callable[[], datetime] | None = None,
        deadline_reference: Callable[[], datetime | None] | None = None,
        monotonic: Callable[[], float] | None = None,
        stabilization_seconds: float = 2.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 15.0,
        max_consecutive_attempts: int = 6,
    ):
        if stabilization_seconds < 0:
            raise ValueError("stabilization_seconds must be non-negative")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("remote support retry interval is invalid")
        if max_consecutive_attempts <= 0:
            raise ValueError("max_consecutive_attempts must be positive")
        self._store = store
        self._credentials = credentials
        self._runtime_dir = Path(runtime_dir)
        self._popen = popen_factory
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self._deadline_reference = (
            deadline_reference
            or (
                (lambda: self._utc_now().astimezone(timezone.utc))
                if utc_now is not None
                else local_deadline_reference
            )
        )
        if monotonic is None:
            import time

            monotonic = time.monotonic
        self._monotonic = monotonic
        self._stabilization_seconds = stabilization_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._max_attempts = max_consecutive_attempts
        self._process = None
        self._process_session_uid: str | None = None
        self._process_started_at = 0.0
        self._output: _BoundedOutputCollector | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="remote-support",
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        with self._lock:
            # Preserve SQLite desired state.  A non-expired session must
            # reconnect after a process or power restart.
            self._terminate_process()
        self._thread = None

    def open_session(
        self,
        *,
        session_uid: str,
        command_uid: str,
        device_name: str,
        remote_port: int,
        expires_at: str,
    ) -> str:
        if self._credentials is None:
            raise RuntimeError("remote support credentials are unavailable")
        disposition = self._store.request_remote_support_open(
            session_uid=session_uid,
            command_uid=command_uid,
            device_name=device_name,
            remote_port=remote_port,
            expires_at=expires_at,
        )
        if disposition == "CONFLICT":
            raise RuntimeError("another remote support session is active")
        self._wake_event.set()
        return disposition

    def close_session(self, *, session_uid: str, command_uid: str) -> str:
        disposition = self._store.request_remote_support_close(
            session_uid,
            command_uid,
        )
        if disposition == "NOT_FOUND":
            raise RuntimeError("remote support session does not match")
        self._wake_event.set()
        return disposition

    def poll_once(self) -> None:
        with self._lock:
            row = self._store.get_remote_support_session()
            if row is None:
                self._terminate_process()
                return
            session_uid = row["session_uid"]
            now = self._utc_now().astimezone(timezone.utc)
            expires_at = _parse_utc(row["expires_at"])
            deadline_reference = self._deadline_reference()
            if (
                deadline_reference is not None
                and expires_at <= deadline_reference
            ):
                self._terminate_process()
                self._store.transition_remote_support_session(
                    session_uid,
                    "EXPIRED",
                )
                return
            if row["state"] == "CLOSING":
                self._terminate_process()
                self._store.transition_remote_support_session(
                    session_uid,
                    "CLOSED",
                )
                return
            if row["state"] in {"CLOSED", "FAILED", "EXPIRED"}:
                self._terminate_process()
                return

            if self._process is not None:
                if self._process_session_uid != session_uid:
                    self._terminate_process()
                else:
                    return_code = self._process.poll()
                    if return_code is not None:
                        self._handle_process_exit(row, return_code)
                        return
                    if (
                        row["state"] == "CONNECTING"
                        and self._monotonic() - self._process_started_at
                        >= self._stabilization_seconds
                    ):
                        self._store.transition_remote_support_session(
                            session_uid,
                            "OPEN",
                        )
                    return

            if row["state"] == "OPEN":
                # The Python process restarted while an OPEN row survived.
                self._store.transition_remote_support_session(
                    session_uid,
                    "CONNECTING",
                    failure_code="SSH_EXITED",
                )
                row = self._store.get_remote_support_session()
            next_attempt_at = row.get("next_attempt_at")
            if next_attempt_at is not None and now.timestamp() < float(next_attempt_at):
                return
            self._spawn(row)

    def build_ssh_argv(self, remote_port: int) -> list[str]:
        if self._credentials is None:
            raise ValueError("remote support credentials are unavailable")
        if remote_port not in REMOTE_PORTS:
            raise ValueError("remote support port must be one of 22011..22014")
        identity, known_hosts = self._prepare_runtime_material()
        credentials = self._credentials
        return [
            "/usr/bin/ssh",
            "-4",
            "-T",
            "-n",
            "-p",
            str(credentials.server_port),
            "-i",
            str(identity),
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UpdateHostKeys=no",
            "-o",
            "VerifyHostKeyDNS=no",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ForwardAgent=no",
            "-o",
            "RequestTTY=no",
            "-o",
            "PermitLocalCommand=no",
            "-o",
            "ControlMaster=no",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "TCPKeepAlive=yes",
            "-o",
            "LogLevel=ERROR",
            "-R",
            f"127.0.0.1:{remote_port}:127.0.0.1:22",
            f"{credentials.server_user}@{credentials.server_host}",
            credentials.lease_guard_command,
        ]

    def _run(self) -> None:
        while not self._stop_event.is_set():
            healthy = self._poll_safely()
            # An unexpected supervisor defect must neither leave a tunnel
            # running without a trustworthy state nor fill journald in a hot
            # loop when SQLite or process supervision remains unhealthy.
            self._wake_event.wait(0.25 if healthy else 5.0)
            self._wake_event.clear()

    def _poll_safely(self) -> bool:
        try:
            self.poll_once()
            return True
        except Exception:
            logger.exception("remote support supervision poll failed")
        try:
            with self._lock:
                row = self._store.get_remote_support_session()
                self._terminate_process()
                if (
                    row is not None
                    and row["state"] not in {"CLOSED", "FAILED", "EXPIRED"}
                ):
                    self._store.transition_remote_support_session(
                        row["session_uid"],
                        "FAILED",
                        failure_code="PROCESS_SUPERVISION_FAILED",
                    )
        except Exception:
            logger.exception(
                "remote support supervision failure could not be persisted"
            )
        return False

    def _spawn(self, row: dict[str, Any]) -> None:
        session_uid = row["session_uid"]
        try:
            argv = self.build_ssh_argv(int(row["remote_port"]))
            process = self._popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                start_new_session=True,
            )
        except FileNotFoundError:
            self._record_start_failure(row, "SSH_NOT_AVAILABLE")
            return
        except (OSError, ValueError):
            self._record_start_failure(row, "SSH_START_FAILED")
            return
        self._process = process
        self._process_session_uid = session_uid
        self._process_started_at = self._monotonic()
        self._output = _BoundedOutputCollector(getattr(process, "stderr", None))

    def _record_start_failure(self, row: dict[str, Any], code: str) -> None:
        self._record_retry_or_fail(row, code)

    def _handle_process_exit(self, row: dict[str, Any], return_code: int) -> None:
        output_size = len(self._output.snapshot()) if self._output else 0
        logger.warning(
            "remote SSH exited: session=%s code=%d capturedBytes=%d",
            row["session_uid"],
            return_code,
            output_size,
        )
        self._clear_process_handles()
        self._record_retry_or_fail(row, "SSH_EXITED")

    def _record_retry_or_fail(self, row: dict[str, Any], code: str) -> None:
        current_attempts = int(row.get("attempt_count") or 0)
        delay = min(
            self._retry_base_seconds * (2 ** current_attempts),
            self._retry_max_seconds,
        )
        attempts = self._store.record_remote_support_retry(
            row["session_uid"],
            next_attempt_at=self._utc_now().timestamp() + delay,
            failure_code=code,
        )
        if attempts >= self._max_attempts:
            self._store.transition_remote_support_session(
                row["session_uid"],
                "FAILED",
                failure_code=code,
            )

    def _prepare_runtime_material(self) -> tuple[Path, Path]:
        credentials = self._credentials
        if credentials is None:
            raise ValueError("remote support credentials are unavailable")
        identity = self._runtime_dir / "id_ed25519"
        known_hosts = self._runtime_dir / "known_hosts"
        atomic_write_bytes(
            identity,
            credentials.identity_private_key.encode("ascii"),
            mode=0o600,
        )
        atomic_write_bytes(
            known_hosts,
            credentials.known_hosts_line().encode("ascii"),
            mode=0o600,
        )
        return identity, known_hosts

    def _terminate_process(self) -> None:
        process = self._process
        running = False
        if process is not None:
            try:
                running = process.poll() is None
            except Exception:
                # A broken process adapter is itself a reason to make a best
                # effort termination before declaring supervision failed.
                running = True
        if process is not None and running:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.error("remote SSH process did not exit after kill")
            except OSError:
                pass
        self._clear_process_handles()

    def _clear_process_handles(self) -> None:
        if self._output is not None:
            self._output.close()
        self._output = None
        self._process = None
        self._process_session_uid = None
        self._process_started_at = 0.0


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("remote support expiresAt must be UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError("remote support expiresAt must be UTC") from error
    return parsed.astimezone(timezone.utc)
