package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.DeviceEnrollmentCrypto;
import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.CloseRemoteSupportRequest;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.OpenRemoteSupportRequest;
import org.enveloping.ecobin.device.web.v1.remote.RemoteSupportModels.RemoteSupportSessionView;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationClaim;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationDigests;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationResult;
import org.enveloping.ecobin.framework.reliability.PlatformDeviceAssetTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliablePlatformDeviceControlTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.port.PlatformMaintenanceSshKeyQueryPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.ActivePlatformMaintenanceSshKey;
import org.enveloping.ecobin.identity.api.result.AuthorizedDeviceScope;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/** Audited short-lived reverse-SSH sessions backed by the four-port lease pool. */
@Service
public class RemoteSupportSessionService {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            RemoteSupportSessionService.class);

    private static final String OPEN_TASK = "OPEN_REMOTE_SUPPORT_TUNNEL";
    private static final String CLOSE_TASK = "CLOSE_REMOTE_SUPPORT_TUNNEL";
    private static final String TARGET_TYPE = "REMOTE_SUPPORT_SESSION";
    private static final String OPEN_ACTION = "device.remote-support.open";
    private static final String CLOSE_ACTION = "device.remote-support.close";
    private static final String GLOBAL_TARGET_TYPE = "remote-support-session";
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";
    static final String LOCK_ASSET_SQL = """
            SELECT asset.id, asset.hardware_sn,
                   asset.lifecycle_status,
                   transport.onenet_connection_status
            FROM dev_device_asset asset
            JOIN dev_device_transport_state transport
              ON transport.asset_id = asset.id
            WHERE asset.hardware_sn = ?
            FOR UPDATE
            """;
    static final String READ_MAINTENANCE_IDENTITY_SQL = """
            SELECT tunnel_public_key, ssh_host_public_key
            FROM dev_device_maintenance_identity
            WHERE asset_id = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeviceScopeAuthorizationPort authorization;
    private final PlatformMaintenanceSshKeyQueryPort sshKeys;
    private final GlobalOperationIdempotencyPort idempotency;
    private final AuditPort auditPort;
    private final PlatformDeviceAssetTaskRefFactory taskRefFactory;
    private final ReliablePlatformDeviceControlTaskRegistrationPort tasks;
    private final ReliableDeviceTaskProofPort taskProof;
    private final DeviceConfigurationCanonicalizer canonicalizer;
    private final RemoteSupportLeaseStore leases;
    private final OpenSshMaintenanceCertificateSigner certificateSigner;
    private final RemoteSupportBootstrapProperties properties;

    public RemoteSupportSessionService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeviceScopeAuthorizationPort authorization,
            PlatformMaintenanceSshKeyQueryPort sshKeys,
            GlobalOperationIdempotencyPort idempotency,
            AuditPort auditPort,
            PlatformDeviceAssetTaskRefFactory taskRefFactory,
            ReliablePlatformDeviceControlTaskRegistrationPort tasks,
            ReliableDeviceTaskProofPort taskProof,
            DeviceConfigurationCanonicalizer canonicalizer,
            RemoteSupportLeaseStore leases,
            OpenSshMaintenanceCertificateSigner certificateSigner,
            RemoteSupportBootstrapProperties properties) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.authorization = authorization;
        this.sshKeys = sshKeys;
        this.idempotency = idempotency;
        this.auditPort = auditPort;
        this.taskRefFactory = taskRefFactory;
        this.tasks = tasks;
        this.taskProof = taskProof;
        this.canonicalizer = canonicalizer;
        this.leases = leases;
        this.certificateSigner = certificateSigner;
        this.properties = properties;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RemoteSupportSessionView open(
            UUID operationUid,
            String hardwareSn,
            OpenRemoteSupportRequest request) {
        requireEnabled();
        requireUuidV4(operationUid, "Idempotency-Key");
        String normalizedHardwareSn = hardwareSn(hardwareSn);
        if (request == null
                || request.maintenanceSshKeyUid() == null
                || request.lifetimeSeconds() == null) {
            throw invalid("维护 SSH 公钥、有效期和原因不能为空");
        }
        requireUuidV4(request.maintenanceSshKeyUid(), "maintenanceSshKeyUid");
        int lifetime = request.lifetimeSeconds();
        if (lifetime < 300 || lifetime > 1800) {
            throw invalid("远程维护有效期必须在 5 到 30 分钟之间");
        }
        String reason = required(request.reason(), 500, "维护原因");
        byte[] requestSha256 = requestFingerprint(
                "OPEN", normalizedHardwareSn,
                request.maintenanceSshKeyUid(), lifetime, reason);
        AuthorizedDeviceScope actor = authorize();
        TargetWebAuditRequestContext.describe(
                "device.remote-support.open", normalizedHardwareSn);
        RemoteSession replay = sessionByOpenOperation(operationUid, false);
        if (replay != null) {
            requireReplayActorAndRequest(
                    replay, actor, requestSha256, false);
            return view(replay);
        }
        GlobalOperationBinding operationBinding = operationBinding(
                operationUid,
                actor,
                OPEN_ACTION,
                normalizedHardwareSn,
                requestSha256);
        GlobalOperationClaim operationClaim = idempotency.claim(
                operationBinding);
        if (operationClaim.replay()) {
            return replayOpenClaim(
                    operationUid,
                    actor,
                    requestSha256,
                    operationClaim.result());
        }
        ActivePlatformMaintenanceSshKey key = sshKeys.resolveActive(
                        actor.principalUid(),
                        request.maintenanceSshKeyUid())
                .orElseThrow(() -> new TargetApiException(
                        422,
                        "IDENTITY.MAINTENANCE_SSH_KEY_UNAVAILABLE",
                        "所选维护 SSH 公钥不存在或已经撤销"));
        Asset asset = lockAsset(normalizedHardwareSn);
        if (!"NORMAL".equals(asset.lifecycleStatus())) {
            throw conflict(
                    "DEVICE.REMOTE_SUPPORT_ASSET_UNAVAILABLE",
                    "禁用或报废设备不能开启远程维护");
        }
        if (!"ONLINE".equals(asset.oneNetStatus())) {
            throw conflict(
                    "DEVICE.REMOTE_SUPPORT_DEVICE_OFFLINE",
                    "设备当前未通过 OneNet 在线，不能开启远程维护");
        }
        if (activeSessionForAsset(asset.id()) != null) {
            throw conflict(
                    "DEVICE.REMOTE_SUPPORT_ALREADY_ACTIVE",
                    "该设备已有进行中的远程维护会话");
        }
        int port = allocatePort();
        LocalDateTime now = databaseNow();
        LocalDateTime expiresAt = now.plusSeconds(lifetime);
        LocalDateTime connectDeadline = now.plusSeconds(
                Math.min(120, lifetime - 30));
        UUID sessionUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        RemoteSupportLeaseStore.KeyParts tunnelKey =
                RemoteSupportLeaseStore.keyParts(asset.tunnelPublicKey());
        RemoteSupportLeaseStore.Lease lease =
                new RemoteSupportLeaseStore.Lease(
                        sessionUid,
                        asset.hardwareSn(),
                        port,
                        tunnelKey.keyType(),
                        tunnelKey.keyBase64(),
                        tunnelKey.fingerprint(),
                        instant(now),
                        instant(expiresAt));

        Long[] sessionId = new Long[1];
        actor.persistenceRef().writeForeignKeysTo(
                (tenantId, organizationId, platformAdminId,
                 staffAccountId) -> {
                    if (platformAdminId == null
                            || staffAccountId != null) {
                        throw forbidden();
                    }
                    key.persistenceRef().writeForeignKeyTo(
                            maintenanceKeyId -> {
                                try {
                                    jdbc.update("""
                                                    INSERT INTO dev_remote_support_session (
                                                        session_uid, operation_uid,
                                                        request_sha256, asset_id,
                                                        requested_by_platform_admin_id,
                                                        requested_by_platform_admin_uid,
                                                        maintenance_ssh_key_id,
                                                        maintenance_ssh_key_uid,
                                                        tunnel_public_key,
                                                        tunnel_fingerprint_sha256,
                                                        ssh_host_public_key,
                                                        reason, state, port_no,
                                                        open_command_uid,
                                                        close_operation_uid,
                                                        close_request_sha256,
                                                        close_command_uid,
                                                        device_reported_state,
                                                        server_lease_state,
                                                        failure_code, failure_detail,
                                                        certificate_serial,
                                                        certificate_text,
                                                        certificate_sha256,
                                                        certificate_issued_at,
                                                        connect_deadline_at,
                                                        expires_at, opened_at,
                                                        close_requested_at,
                                                        closed_at,
                                                        lease_released_at,
                                                        lock_version,
                                                        created_at, updated_at
                                                    ) VALUES (
                                                        ?, ?, ?, ?, ?, ?, ?, ?,
                                                        ?, ?, ?, ?,
                                                        'PREPARING', ?, ?,
                                                        NULL, NULL, NULL, NULL,
                                                        'DESIRED', NULL, NULL,
                                                        NULL, NULL, NULL, NULL,
                                                        ?, ?, NULL, NULL, NULL,
                                                        NULL,
                                                        0, ?, ?
                                                    )
                                                    """,
                                            sessionUid.toString(),
                                            operationUid.toString(),
                                            requestSha256,
                                            asset.id(),
                                            platformAdminId,
                                            actor.principalUid().toString(),
                                            maintenanceKeyId,
                                            key.maintenanceSshKeyUid()
                                                    .toString(),
                                            asset.tunnelPublicKey(),
                                            DeviceEnrollmentCrypto
                                                    .sshFingerprint(
                                                            asset.tunnelPublicKey()),
                                            asset.hostPublicKey(),
                                            reason,
                                            port,
                                            commandUid.toString(),
                                            connectDeadline,
                                            expiresAt,
                                            now,
                                            now);
                                } catch (DuplicateKeyException collision) {
                                    throw conflict(
                                            "DEVICE.REMOTE_SUPPORT_SLOT_CONFLICT",
                                            "设备或远程维护端口刚被另一会话占用，请重试");
                                }
                                sessionId[0] = jdbc.queryForObject("""
                                                SELECT id
                                                FROM dev_remote_support_session
                                                WHERE session_uid = ?
                                                """,
                                        Long.class,
                                        sessionUid.toString());
                                appendAudit(
                                        operationUid,
                                        actor,
                                        platformAdminId,
                                        "device.remote-support.open",
                                        sessionUid,
                                        normalizedHardwareSn,
                                        reason,
                                        requestSha256,
                                        now);
                            });
                });
        registerOpenTask(
                asset, sessionUid, commandUid, port, now, expiresAt);
        requireSingle(jdbc.update("""
                        UPDATE dev_remote_support_session
                        SET state = 'CONNECTING', updated_at = ?,
                            lock_version = lock_version + 1
                        WHERE id = ? AND state = 'PREPARING'
                        """,
                now,
                sessionId[0]), "start remote support connection");
        RemoteSession created = requireSession(
                sessionUid, actor.principalUid(), false);
        succeedOperation(operationUid, created);
        registerAfterCommitLeasePublish(lease);
        return view(created);
    }

    @Transactional(readOnly = true)
    public RemoteSupportSessionView detail(UUID sessionUid) {
        requireUuidV4(sessionUid, "sessionUid");
        AuthorizedDeviceScope actor = authorize();
        return view(requireSession(sessionUid, actor.principalUid(), false));
    }

    @Transactional(readOnly = true)
    public RemoteSupportSessionView currentForAsset(String hardwareSn) {
        String normalizedHardwareSn = hardwareSn(hardwareSn);
        AuthorizedDeviceScope actor = authorize();
        RemoteSession session = sessionQuery(
                "asset.hardware_sn = ?"
                        + " AND session.requested_by_platform_admin_uid = ?"
                        + " AND session.active_asset_id IS NOT NULL",
                false,
                normalizedHardwareSn,
                actor.principalUid().toString());
        if (session == null) {
            throw notFound();
        }
        return view(session);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public RemoteSupportSessionView close(
            UUID operationUid,
            UUID sessionUid,
            CloseRemoteSupportRequest request) {
        requireEnabled();
        requireUuidV4(operationUid, "Idempotency-Key");
        requireUuidV4(sessionUid, "sessionUid");
        String reason = request == null
                ? null : required(request.reason(), 500, "关闭原因");
        byte[] requestSha256 = requestFingerprint(
                "CLOSE", sessionUid.toString(), null, 0, reason);
        AuthorizedDeviceScope actor = authorize();
        TargetWebAuditRequestContext.describe(
                "device.remote-support.close", sessionUid.toString());
        RemoteSession replay = sessionByCloseOperation(operationUid, false);
        if (replay != null) {
            requireCloseReplay(
                    replay,
                    operationUid,
                    sessionUid,
                    actor,
                    requestSha256);
            return view(replay);
        }
        GlobalOperationClaim operationClaim = idempotency.claim(
                operationBinding(
                        operationUid,
                        actor,
                        CLOSE_ACTION,
                        sessionUid.toString(),
                        requestSha256));
        if (operationClaim.replay()) {
            return replayCloseClaim(
                    operationUid,
                    sessionUid,
                    actor,
                    requestSha256,
                    operationClaim.result());
        }
        RemoteSession session = requireSession(
                sessionUid, actor.principalUid(), true);
        if (session.closeOperationUid() != null) {
            requireCloseReplay(
                    session,
                    operationUid,
                    sessionUid,
                    actor,
                    requestSha256);
            succeedOperation(operationUid, session);
            return view(session);
        }
        if (isTerminal(session.state())) {
            succeedOperation(operationUid, session);
            return view(session);
        }
        UUID closeCommandUid = UUID.randomUUID();
        LocalDateTime now = databaseNow();
        actor.persistenceRef().writeForeignKeysTo(
                (tenantId, organizationId, platformAdminId,
                 staffAccountId) -> {
                    if (!Objects.equals(
                            platformAdminId,
                            session.requestedByPlatformAdminId())) {
                        throw forbidden();
                    }
                    requireSingle(jdbc.update("""
                                    UPDATE dev_remote_support_session
                                    SET state = 'CLOSING',
                                        close_operation_uid = ?,
                                        close_request_sha256 = ?,
                                        close_command_uid = ?,
                                        close_requested_at = ?,
                                        lock_version = lock_version + 1,
                                        updated_at = ?
                                    WHERE id = ?
                                      AND state IN (
                                          'PREPARING', 'CONNECTING', 'OPEN',
                                          'RECONNECTING'
                                      )
                                    """,
                            operationUid.toString(),
                            requestSha256,
                            closeCommandUid.toString(),
                            now,
                            now,
                            session.id()),
                            "request remote support close");
                    appendAudit(
                            operationUid,
                            actor,
                            platformAdminId,
                            "device.remote-support.close",
                            sessionUid,
                            session.hardwareSn(),
                            reason,
                            requestSha256,
                            now);
                });
        registerCloseTask(session, closeCommandUid, now);
        RemoteSession closing = requireSession(
                sessionUid, actor.principalUid(), false);
        succeedOperation(operationUid, closing);
        registerAfterCommitLeaseRevoke(sessionUid, session.port());
        return view(closing);
    }

    /** Applies a trusted, platform-scoped status event in the inbox transaction. */
    public boolean applyStatus(long sourceInboxId, JsonNode normalized) {
        JsonNode source = requiredObject(normalized, "trustedSource");
        JsonNode event = requiredObject(normalized, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        String hardwareSn = requiredText(source, "deviceName", 64);
        requireText(event, "eventType", "REMOTE_SUPPORT_TUNNEL_STATUS");
        requireText(target, "type", "DEVICE_ASSET");
        requireText(target, "uid", hardwareSn);
        UUID eventUid = UUID.fromString(
                requiredPattern(event, "eventUid", UUID_V4));
        UUID commandUid = UUID.fromString(
                requiredPattern(event, "commandUid", UUID_V4));
        UUID sessionUid = UUID.fromString(
                requiredPattern(payload, "sessionUid", UUID_V4));
        String state = requiredText(payload, "state", 16);
        if (!List.of(
                "CONNECTING", "OPEN", "CLOSED", "FAILED", "EXPIRED")
                .contains(state)) {
            throw new IllegalArgumentException(
                    "remote support state is invalid");
        }
        int port = requiredInteger(payload, "remotePort", 22011, 22014);
        String failureCode = nullableText(payload, "failureCode", 64);
        if (failureCode != null
                && !failureCode.matches("^[A-Z][A-Z0-9_]{0,63}$")) {
            throw new IllegalArgumentException(
                    "remote support failureCode format is invalid");
        }
        if ("FAILED".equals(state) && failureCode == null) {
            throw new IllegalArgumentException(
                    "remote support failureCode shape is invalid");
        }
        String canonicalSha256 = requiredPattern(
                normalized, "eventCanonicalSha256", SHA256);
        LocalDateTime occurredAt = timestamp(event, "occurredAt");
        LocalDateTime receivedAt = databaseNow();
        if (occurredAt.isAfter(receivedAt)) {
            throw new IllegalArgumentException(
                    "remote support status occurred in the future");
        }
        Integer existing = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_remote_support_status_event
                        WHERE source_inbox_id = ? OR event_uid = ?
                        """,
                Integer.class,
                sourceInboxId,
                eventUid.toString());
        if (existing != null && existing > 0) {
            return false;
        }
        RemoteSession session = sessionForStatus(
                sessionUid, hardwareSn, true);
        if (session.port() != port) {
            throw new IllegalArgumentException(
                    "remote support status port differs from its session");
        }
        boolean provesOpen = commandUid.equals(session.openCommandUid());
        boolean provesClose = commandUid.equals(session.closeCommandUid());
        if (!provesOpen && !provesClose) {
            throw new IllegalArgumentException(
                    "remote support status command differs from its session");
        }
        if (("CONNECTING".equals(state) || "OPEN".equals(state))
                && !provesOpen) {
            throw new IllegalArgumentException(
                    "remote support active state must prove the open command");
        }
        if ("CLOSED".equals(state) && !provesClose) {
            throw new IllegalArgumentException(
                    "remote support closed state must prove the close command");
        }
        jdbc.update("""
                        INSERT INTO dev_remote_support_status_event (
                            event_uid, session_id, source_inbox_id,
                            reported_state, failure_code, ssh_exit_code,
                            event_sha256, occurred_at, received_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                        """,
                eventUid.toString(),
                session.id(),
                sourceInboxId,
                state,
                failureCode,
                HexFormat.of().parseHex(canonicalSha256),
                occurredAt,
                receivedAt,
                receivedAt);
        boolean changed;
        if ("FAILED".equals(state)
                || "CLOSED".equals(state)
                || "EXPIRED".equals(state)) {
            registerAfterCommitLeaseRevoke(sessionUid, port);
            String terminal = "FAILED".equals(state)
                    ? "FAILED" : state;
            changed = jdbc.update("""
                            UPDATE dev_remote_support_session
                            SET state = ?, device_reported_state = ?,
                                server_lease_state = 'REVOKED',
                                failure_code = ?, failure_detail = NULL,
                                closed_at = COALESCE(closed_at, ?),
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND state IN (
                                  'PREPARING', 'CONNECTING', 'OPEN',
                                  'RECONNECTING', 'CLOSING'
                              )
                            """,
                    terminal,
                    state,
                    failureCode,
                    receivedAt,
                    receivedAt,
                    session.id()) == 1;
            if (!changed && isTerminal(session.state())) {
                changed = refreshTerminalDeviceReportedState(
                        session.id(), receivedAt);
            }
        } else {
            changed = jdbc.update("""
                            UPDATE dev_remote_support_session
                            SET state = CASE
                                    WHEN state = 'PREPARING'
                                    THEN 'CONNECTING'
                                    ELSE state
                                END,
                                device_reported_state = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND state IN (
                                  'PREPARING', 'CONNECTING', 'OPEN',
                                  'RECONNECTING'
                              )
                            """,
                    state,
                    receivedAt,
                    session.id()) == 1;
        }
        taskProof.completeFromTrustedProof(
                provesClose ? CLOSE_TASK : OPEN_TASK,
                TARGET_TYPE,
                sessionUid.toString());
        return changed;
    }

    private boolean refreshTerminalDeviceReportedState(
            long sessionId,
            LocalDateTime receivedAt) {
        return jdbc.update("""
                        UPDATE dev_remote_support_session
                        SET device_reported_state = (
                                SELECT latest.reported_state
                                FROM dev_remote_support_status_event latest
                                WHERE latest.session_id = ?
                                ORDER BY latest.occurred_at DESC,
                                         latest.id DESC
                                LIMIT 1
                            ),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND state IN ('CLOSED', 'FAILED', 'EXPIRED')
                          AND (
                              device_reported_state IS NULL
                              OR device_reported_state <> (
                                  SELECT latest.reported_state
                                  FROM dev_remote_support_status_event latest
                                  WHERE latest.session_id = ?
                                  ORDER BY latest.occurred_at DESC,
                                           latest.id DESC
                                  LIMIT 1
                              )
                          )
                        """,
                sessionId,
                receivedAt,
                sessionId,
                sessionId) == 1;
    }

    @Scheduled(
            initialDelayString =
                    "${ecobin.remote-support.reconcile-initial-delay-ms:2000}",
            fixedDelayString =
                    "${ecobin.remote-support.reconcile-delay-ms:2000}")
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public void reconcile() {
        if (!properties.isEnabled()) {
            return;
        }
        List<ReservedSession> sessions = jdbc.query("""
                        SELECT session_uid, port_no
                        FROM dev_remote_support_session
                        WHERE lease_released_at IS NULL
                        ORDER BY id
                        """,
                (rs, ignored) -> new ReservedSession(
                        UUID.fromString(rs.getString("session_uid")),
                        rs.getInt("port_no")));
        for (int port : List.of(22011, 22012, 22013, 22014)) {
            if (sessions.stream().noneMatch(row -> row.port() == port)) {
                leases.removeDesired(port);
            }
        }
        for (ReservedSession session : sessions) {
            reconcileOne(session.sessionUid());
        }
    }

    private void reconcileOne(UUID sessionUid) {
        RemoteSession session = sessionForReconciliation(sessionUid, true);
        if (session == null || session.leaseReleasedAt() != null) {
            return;
        }
        LocalDateTime now = databaseNow();
        if (!isTerminal(session.state())
                && !session.expiresAt().isAfter(now)) {
            terminateAndReconcileRelease(
                    session, "EXPIRED", null, now);
            return;
        }
        Optional<ActivePlatformMaintenanceSshKey> activeKey = Optional.empty();
        if (!isTerminal(session.state())) {
            activeKey = sshKeys.resolveActive(
                    session.requestedByPlatformAdminUid(),
                    session.maintenanceSshKeyUid());
        }
        if (!isTerminal(session.state()) && activeKey.isEmpty()) {
            terminateAndReconcileRelease(
                    session,
                    "FAILED",
                    "MAINTENANCE_SSH_KEY_REVOKED",
                    now);
            return;
        }

        boolean desiredRequired = List.of(
                "PREPARING", "CONNECTING", "OPEN", "RECONNECTING")
                .contains(session.state());
        if (desiredRequired) {
            leases.synchronizeDesired(expectedLease(session));
        } else {
            leases.removeDesired(session.port());
        }

        RemoteSupportReconciliationPolicy.ActualLeaseState actual =
                leases.inspectActual(
                sessionUid,
                session.hardwareSn(),
                session.port(),
                session.tunnelFingerprint(),
                instant(session.expiresAt()));

        RemoteSupportReconciliationPolicy.Decision decision =
                RemoteSupportReconciliationPolicy.decide(
                        session.state(), actual);
        if (decision.failureCode() != null) {
            leases.removeDesired(session.port());
            markTerminal(
                    session, "FAILED", decision.failureCode(), now);
            updateServerLeaseState(session.id(), "ERROR", now);
            return;
        }

        if (decision.releaseLease()) {
            releaseLease(session, decision.nextState(), now);
            return;
        }
        if (isTerminal(session.state())) {
            updateServerLeaseState(
                    session.id(),
                    actual == RemoteSupportReconciliationPolicy
                            .ActualLeaseState.CONFLICT
                            ? "ERROR" : "REVOKED",
                    now);
            return;
        }
        if ("CLOSING".equals(session.state())) {
            updateServerLeaseState(session.id(), "REVOKED", now);
            return;
        }
        if (!decision.nextState().equals(session.state())) {
            requireSingle(jdbc.update("""
                            UPDATE dev_remote_support_session
                            SET state = ?, server_lease_state = ?,
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ? AND state = ?
                            """,
                    decision.nextState(),
                    "OPEN".equals(decision.nextState())
                            ? "ACTIVE" : "DESIRED",
                    now,
                    session.id(),
                    session.state()), "transition remote support lease");
            return;
        }

        if (("PREPARING".equals(session.state())
                || "CONNECTING".equals(session.state()))
                && !session.connectDeadlineAt().isAfter(now)
                && !(actual == RemoteSupportReconciliationPolicy
                        .ActualLeaseState.MATCH && "OPEN".equals(
                        session.deviceReportedState()))) {
            terminateAndReconcileRelease(
                    session, "FAILED", "CONNECT_TIMEOUT", now);
            return;
        }
        if (actual == RemoteSupportReconciliationPolicy
                    .ActualLeaseState.MATCH
                && "OPEN".equals(session.deviceReportedState())
                && ("PREPARING".equals(session.state())
                    || "CONNECTING".equals(session.state()))) {
            ActivePlatformMaintenanceSshKey key = activeKey.orElseThrow();
            Instant validAfter = instant(now).minusSeconds(30);
            Instant validBefore = min(
                    instant(session.expiresAt()),
                    instant(now).plusSeconds(1770));
            String certificate = certificateSigner.sign(
                    session.id(),
                    session.requestedByPlatformAdminUid(),
                    session.sessionUid(),
                    session.hardwareSn(),
                    key.publicKey(),
                    validAfter,
                    validBefore);
            jdbc.update("""
                            UPDATE dev_remote_support_session
                            SET state = 'OPEN',
                                server_lease_state = 'ACTIVE',
                                certificate_serial = ?,
                                certificate_text = ?,
                                certificate_sha256 = ?,
                                certificate_issued_at = ?,
                                opened_at = COALESCE(opened_at, ?),
                                lock_version = lock_version + 1,
                                updated_at = ?
                            WHERE id = ?
                              AND state IN ('PREPARING', 'CONNECTING')
                            """,
                    session.id(),
                    certificate,
                    sha256(certificate.getBytes(StandardCharsets.US_ASCII)),
                    now,
                    now,
                    now,
                    session.id());
        }
    }

    private RemoteSupportLeaseStore.Lease expectedLease(
            RemoteSession session) {
        RemoteSupportLeaseStore.KeyParts key =
                RemoteSupportLeaseStore.keyParts(
                        session.tunnelPublicKey());
        return new RemoteSupportLeaseStore.Lease(
                session.sessionUid(),
                session.hardwareSn(),
                session.port(),
                key.keyType(),
                key.keyBase64(),
                key.fingerprint(),
                instant(session.createdAt()),
                instant(session.expiresAt()));
    }

    private void terminateAndReconcileRelease(
            RemoteSession session,
            String terminalState,
            String failureCode,
            LocalDateTime now) {
        leases.removeDesired(session.port());
        markTerminal(session, terminalState, failureCode, now);
        RemoteSupportReconciliationPolicy.ActualLeaseState actual =
                leases.inspectActual(
                        session.sessionUid(),
                        session.hardwareSn(),
                        session.port(),
                        session.tunnelFingerprint(),
                        instant(session.expiresAt()));
        if (actual == RemoteSupportReconciliationPolicy
                .ActualLeaseState.ABSENT) {
            releaseLease(session, terminalState, now);
        } else if (actual == RemoteSupportReconciliationPolicy
                .ActualLeaseState.CONFLICT) {
            updateServerLeaseState(session.id(), "ERROR", now);
        }
    }

    private void releaseLease(
            RemoteSession session,
            String state,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        UPDATE dev_remote_support_session
                        SET state = ?, server_lease_state = 'ABSENT',
                            closed_at = COALESCE(closed_at, ?),
                            lease_released_at = COALESCE(
                                lease_released_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lease_released_at IS NULL
                        """,
                state,
                now,
                now,
                now,
                session.id()), "release remote support lease");
    }

    private void updateServerLeaseState(
            long sessionId,
            String state,
            LocalDateTime now) {
        jdbc.update("""
                        UPDATE dev_remote_support_session
                        SET server_lease_state = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ? AND lease_released_at IS NULL
                        """,
                state,
                now,
                sessionId);
    }

    private void registerOpenTask(
            Asset asset,
            UUID sessionUid,
            UUID commandUid,
            int port,
            LocalDateTime issuedAt,
            LocalDateTime expiresAt) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sessionUid", sessionUid.toString());
        payload.put("remotePort", port);
        payload.put("expiresAt", timestamp(expiresAt));
        registerTask(
                OPEN_TASK,
                asset,
                sessionUid,
                commandUid,
                issuedAt,
                expiresAt,
                payload);
    }

    private void registerCloseTask(
            RemoteSession session,
            UUID commandUid,
            LocalDateTime issuedAt) {
        Asset asset = new Asset(
                session.assetId(),
                session.hardwareSn(),
                null,
                null,
                null,
                session.hostPublicKey());
        Map<String, Object> payload = Map.of(
                "sessionUid", session.sessionUid().toString());
        registerTask(
                CLOSE_TASK,
                asset,
                session.sessionUid(),
                commandUid,
                issuedAt,
                issuedAt.plusMinutes(5),
                payload);
    }

    private void registerTask(
            String taskType,
            Asset asset,
            UUID sessionUid,
            UUID commandUid,
            LocalDateTime issuedAt,
            LocalDateTime expiresAt,
            Map<String, Object> payload) {
        Map<String, Object> target = Map.of(
                "type", TARGET_TYPE,
                "uid", sessionUid.toString());
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 2);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", taskType);
        envelope.put("targetDeviceName", asset.hardwareSn());
        envelope.put("target", target);
        envelope.put("issuedAt", timestamp(issuedAt));
        envelope.put("expiresAt", timestamp(expiresAt));
        envelope.put("payloadSchemaVersion", 2);
        envelope.put("payloadSha256",
                canonicalizer.hex(canonicalizer.payloadSha256(payload)));
        envelope.put("payload", payload);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        tasks.register(new ReliablePlatformDeviceControlTaskRegistration(
                taskType,
                taskType + ":" + sessionUid.toString().toUpperCase(),
                TARGET_TYPE,
                sessionUid.toString(),
                taskRefFactory.issue(asset.id()),
                2,
                objectMapper.writeValueAsString(envelope),
                envelopeSha256,
                sessionUid,
                commandUid,
                1000));
    }

    private AuthorizedDeviceScope authorize() {
        AuthorizedDeviceScope actor = authorization.authorize(
                new DeviceScopeAuthorizationQuery(
                        true, null, null, "device.manage"));
        if (!actor.platformActor()) {
            throw forbidden();
        }
        return actor;
    }

    private Asset lockAsset(String hardwareSn) {
        LockedAsset locked = jdbc.query(LOCK_ASSET_SQL,
                (rs, ignored) -> new LockedAsset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("lifecycle_status"),
                        rs.getString("onenet_connection_status")),
                hardwareSn).stream().findFirst()
                .orElseThrow(RemoteSupportSessionService
                        ::remoteSupportIdentityRequired);
        MaintenanceIdentity identity = jdbc.query(
                READ_MAINTENANCE_IDENTITY_SQL,
                (rs, ignored) -> new MaintenanceIdentity(
                        rs.getString("tunnel_public_key"),
                        rs.getString("ssh_host_public_key")),
                locked.id()).stream().findFirst()
                .orElseThrow(RemoteSupportSessionService
                        ::remoteSupportIdentityRequired);
        return new Asset(
                locked.id(),
                locked.hardwareSn(),
                locked.lifecycleStatus(),
                locked.oneNetStatus(),
                identity.tunnelPublicKey(),
                identity.hostPublicKey());
    }

    private int allocatePort() {
        List<Integer> candidates = jdbc.query("""
                        SELECT slot.port_no
                        FROM dev_remote_support_port_slot slot
                        LEFT JOIN dev_remote_support_session active
                          ON active.active_port_no = slot.port_no
                        WHERE slot.enabled = 1
                          AND active.id IS NULL
                        ORDER BY slot.port_no
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getInt("port_no"));
        for (int candidate : candidates) {
            leases.removeDesired(candidate);
            if (leases.portIsClear(candidate)) {
                return candidate;
            }
        }
        throw conflict(
                "DEVICE.REMOTE_SUPPORT_PORT_POOL_EXHAUSTED",
                "远程维护端口当前均被占用或仍在清理，请稍后重试");
    }

    private RemoteSession activeSessionForAsset(long assetId) {
        return jdbc.query("""
                        SELECT session.*, asset.hardware_sn
                        FROM dev_remote_support_session session
                        JOIN dev_device_asset asset ON asset.id = session.asset_id
                        WHERE session.active_asset_id = ?
                        """,
                RemoteSupportSessionService::mapSession,
                assetId).stream().findFirst().orElse(null);
    }

    private RemoteSession sessionByOpenOperation(
            UUID operationUid,
            boolean forUpdate) {
        return sessionQuery(
                "session.operation_uid = ?",
                forUpdate,
                operationUid.toString());
    }

    private RemoteSession sessionByCloseOperation(
            UUID operationUid,
            boolean forUpdate) {
        return sessionQuery(
                "session.close_operation_uid = ?",
                forUpdate,
                operationUid.toString());
    }

    private RemoteSession requireSession(
            UUID sessionUid,
            UUID actorUid,
            boolean forUpdate) {
        RemoteSession session = sessionQuery(
                "session.session_uid = ?",
                forUpdate,
                sessionUid.toString());
        if (session == null
                || !actorUid.equals(session.requestedByPlatformAdminUid())) {
            throw notFound();
        }
        return session;
    }

    private RemoteSession sessionForStatus(
            UUID sessionUid,
            String hardwareSn,
            boolean forUpdate) {
        RemoteSession session = sessionQuery(
                "session.session_uid = ? AND asset.hardware_sn = ?",
                forUpdate,
                sessionUid.toString(),
                hardwareSn);
        if (session == null) {
            throw new IllegalArgumentException(
                    "remote support status session does not exist");
        }
        return session;
    }

    private RemoteSession sessionForReconciliation(
            UUID sessionUid,
            boolean forUpdate) {
        return sessionQuery(
                "session.session_uid = ?",
                forUpdate,
                sessionUid.toString());
    }

    private RemoteSession sessionQuery(
            String predicate,
            boolean forUpdate,
            Object... arguments) {
        String sql = """
                SELECT session.*, asset.hardware_sn
                FROM dev_remote_support_session session
                JOIN dev_device_asset asset ON asset.id = session.asset_id
                WHERE
                """ + predicate + (forUpdate ? " FOR UPDATE" : "");
        return jdbc.query(sql,
                RemoteSupportSessionService::mapSession,
                arguments).stream().findFirst().orElse(null);
    }

    private static RemoteSession mapSession(
            java.sql.ResultSet rs,
            int ignored) throws java.sql.SQLException {
        return new RemoteSession(
                rs.getLong("id"),
                UUID.fromString(rs.getString("session_uid")),
                UUID.fromString(rs.getString("operation_uid")),
                rs.getBytes("request_sha256"),
                rs.getLong("asset_id"),
                rs.getLong("requested_by_platform_admin_id"),
                UUID.fromString(rs.getString(
                        "requested_by_platform_admin_uid")),
                UUID.fromString(rs.getString("maintenance_ssh_key_uid")),
                rs.getString("state"),
                rs.getInt("port_no"),
                rs.getString("device_reported_state"),
                UUID.fromString(rs.getString("open_command_uid")),
                nullableUuid(rs.getString("close_operation_uid")),
                rs.getBytes("close_request_sha256"),
                nullableUuid(rs.getString("close_command_uid")),
                rs.getString("failure_code"),
                rs.getString("certificate_text"),
                rs.getObject("connect_deadline_at", LocalDateTime.class),
                rs.getObject("expires_at", LocalDateTime.class),
                rs.getObject("opened_at", LocalDateTime.class),
                rs.getObject("closed_at", LocalDateTime.class),
                rs.getObject("lease_released_at", LocalDateTime.class),
                rs.getObject("created_at", LocalDateTime.class),
                rs.getLong("lock_version"),
                rs.getString("hardware_sn"),
                rs.getString("tunnel_public_key"),
                "SHA256:" + java.util.Base64.getEncoder()
                        .withoutPadding().encodeToString(
                                rs.getBytes("tunnel_fingerprint_sha256")),
                rs.getString("ssh_host_public_key"));
    }

    private RemoteSupportSessionView view(RemoteSession session) {
        String hostKeyAlias = "ecobin-" + session.hardwareSn();
        String command = "ssh -J " + properties.getJumpUser()
                + "@" + properties.getTunnelHost()
                + ":" + properties.getTunnelSshPort()
                + " -p " + session.port()
                + " -o HostKeyAlias=" + hostKeyAlias
                + " ecobin-maintenance@127.0.0.1";
        return new RemoteSupportSessionView(
                session.sessionUid(),
                session.hardwareSn(),
                session.maintenanceSshKeyUid(),
                session.state(),
                session.port(),
                instant(session.connectDeadlineAt()),
                instant(session.expiresAt()),
                nullableInstant(session.openedAt()),
                nullableInstant(session.closedAt()),
                nullableInstant(session.leaseReleasedAt()),
                session.leaseReleasedAt() == null
                        && ("CLOSING".equals(session.state())
                            || isTerminal(session.state())),
                session.failureCode(),
                session.certificate(),
                properties.getTunnelHost(),
                properties.getTunnelSshPort(),
                properties.getJumpUser(),
                "ecobin-maintenance",
                hostKeyAlias,
                hostKeyAlias + " " + session.hostPublicKey(),
                command,
                session.version());
    }

    private void markTerminal(
            RemoteSession session,
            String terminalState,
            String failureCode,
            LocalDateTime now) {
        jdbc.update("""
                        UPDATE dev_remote_support_session
                        SET state = ?, server_lease_state = 'REVOKED',
                            failure_code = ?, failure_detail = NULL,
                            closed_at = COALESCE(closed_at, ?),
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                terminalState,
                failureCode,
                now,
                now,
                session.id());
    }

    private void appendAudit(
            UUID operationUid,
            AuthorizedDeviceScope actor,
            long platformAdminId,
            String action,
            UUID sessionUid,
            String hardwareSn,
            String reason,
            byte[] requestSha256,
            LocalDateTime occurredAt) {
        Map<String, Object> summary = Map.of(
                "fingerprint", HexFormat.of().formatHex(requestSha256),
                "sessionUid", sessionUid.toString(),
                "hardwareSn", hardwareSn);
        auditPort.append(new AuditEntry(
                UUID.randomUUID(), UUID.randomUUID(), operationUid,
                AuditScopeKind.PLATFORM, null, null,
                AuditActorKind.PLATFORM_ADMIN, platformAdminId,
                null, null, null, actor.actorDisplayName(), action,
                "remote-support-session", sessionUid.toString(),
                "WEB", "SUCCEEDED", actor.sessionUid(), reason,
                objectMapper.writeValueAsString(summary),
                instant(occurredAt)));
    }

    void registerAfterCommitLeasePublish(
            RemoteSupportLeaseStore.Lease lease) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCommit() {
                        try {
                            leases.synchronizeDesired(lease);
                        } catch (RuntimeException failure) {
                            LOGGER.warn(
                                    "remote support desired lease will be "
                                            + "repaired by reconciliation: session={}",
                                    lease.sessionUid(),
                                    failure);
                        }
                    }
                });
    }

    void registerAfterCommitLeaseRevoke(
            UUID sessionUid,
            int port) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCommit() {
                        try {
                            // A delayed terminal status for an already
                            // released session must never delete a newer
                            // session that has since reused this port.
                            leases.revoke(sessionUid, port);
                        } catch (RuntimeException failure) {
                            LOGGER.warn(
                                    "remote support desired lease revoke will "
                                            + "be retried by reconciliation: "
                                            + "session={}",
                                    sessionUid,
                                    failure);
                        }
                    }
                });
    }

    private GlobalOperationBinding operationBinding(
            UUID operationUid,
            AuthorizedDeviceScope actor,
            String action,
            String targetStableKey,
            byte[] requestSha256) {
        return new GlobalOperationBinding(
                operationUid,
                "PLATFORM_ADMIN",
                actor.principalUid(),
                GlobalOperationDigests.platformScope(),
                action,
                GLOBAL_TARGET_TYPE,
                targetStableKey,
                HexFormat.of().formatHex(requestSha256));
    }

    private RemoteSupportSessionView replayOpenClaim(
            UUID operationUid,
            AuthorizedDeviceScope actor,
            byte[] requestSha256,
            GlobalOperationResult result) {
        RemoteSession session = requireSession(
                result.resourceUid(), actor.principalUid(), false);
        if (!operationUid.equals(session.operationUid())) {
            throw new IllegalStateException(
                    "global open result references another operation");
        }
        requireReplayActorAndRequest(
                session, actor, requestSha256, false);
        return view(session);
    }

    private RemoteSupportSessionView replayCloseClaim(
            UUID operationUid,
            UUID requestedSessionUid,
            AuthorizedDeviceScope actor,
            byte[] requestSha256,
            GlobalOperationResult result) {
        if (!requestedSessionUid.equals(result.resourceUid())) {
            throw new IllegalStateException(
                    "global close result references another session");
        }
        RemoteSession session = requireSession(
                result.resourceUid(), actor.principalUid(), false);
        if (session.closeOperationUid() == null) {
            if (!isTerminal(session.state())) {
                throw new IllegalStateException(
                        "global close result has no completed domain close");
            }
            return view(session);
        }
        requireCloseReplay(
                session,
                operationUid,
                requestedSessionUid,
                actor,
                requestSha256);
        return view(session);
    }

    private void requireCloseReplay(
            RemoteSession session,
            UUID operationUid,
            UUID requestedSessionUid,
            AuthorizedDeviceScope actor,
            byte[] requestSha256) {
        if (!requestedSessionUid.equals(session.sessionUid())
                || !operationUid.equals(session.closeOperationUid())) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同远程维护会话");
        }
        requireReplayActorAndRequest(
                session, actor, requestSha256, true);
    }

    private void succeedOperation(
            UUID operationUid,
            RemoteSession session) {
        idempotency.succeed(
                operationUid,
                new GlobalOperationResult(
                        session.sessionUid(),
                        session.state(),
                        session.version()));
    }

    private void requireReplayActorAndRequest(
            RemoteSession session,
            AuthorizedDeviceScope actor,
            byte[] requestSha256,
            boolean close) {
        byte[] prior = close
                ? session.closeRequestSha256()
                : session.requestSha256();
        if (!actor.principalUid().equals(
                session.requestedByPlatformAdminUid())
                || prior == null
                || !MessageDigest.isEqual(prior, requestSha256)) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求或操作者");
        }
    }

    private byte[] requestFingerprint(
            String action,
            String target,
            UUID keyUid,
            int lifetime,
            String reason) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schemaVersion", 1);
        value.put("action", action);
        value.put("target", target);
        value.put("maintenanceSshKeyUid",
                keyUid == null ? null : keyUid.toString());
        value.put("lifetimeSeconds", lifetime);
        value.put("reason", reason);
        return sha256(objectMapper.writeValueAsBytes(value));
    }

    private void requireEnabled() {
        if (!properties.isEnabled()) {
            throw new TargetApiException(
                    503,
                    "DEVICE.REMOTE_SUPPORT_DISABLED",
                    "按需远程维护当前未启用");
        }
    }

    private static String hardwareSn(String value) {
        String normalized = value == null ? "" : value.trim();
        if (!normalized.matches("^[A-Za-z0-9_-]{8,64}$")) {
            throw invalid("硬件序列号格式无效");
        }
        return normalized;
    }

    private static String required(
            String value,
            int maximum,
            String field) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > maximum) {
            throw invalid(field + "不能为空且不能超过 "
                    + maximum + " 个字符");
        }
        return normalized;
    }

    private static boolean isTerminal(String state) {
        return List.of("CLOSED", "FAILED", "EXPIRED").contains(state);
    }

    private static Instant min(Instant first, Instant second) {
        return first.isBefore(second) ? first : second;
    }

    private static String timestamp(LocalDateTime value) {
        return DateTimeFormatter.ISO_INSTANT.format(instant(value));
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static Instant nullableInstant(LocalDateTime value) {
        return value == null ? null : instant(value);
    }

    private static UUID nullableUuid(String value) {
        return value == null ? null : UUID.fromString(value);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }

    private static void requireUuidV4(UUID value, String field) {
        if (value == null || value.version() != 4) {
            throw invalid(field + " 必须是 UUIDv4");
        }
    }

    private static void requireSingle(int rows, String action) {
        if (rows != 1) {
            throw new IllegalStateException(
                    action + " affected " + rows + " rows");
        }
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximum) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredPattern(
            JsonNode parent,
            String field,
            String pattern) {
        String value = requiredText(parent, field, 128);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(field + " has invalid format");
        }
        return value;
    }

    private static void requireText(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(requiredText(parent, field, 128))) {
            throw new IllegalArgumentException(
                    field + " differs from trusted value");
        }
    }

    private static String nullableText(
            JsonNode parent,
            String field,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || value.asText().isBlank()
                || value.asText().length() > maximum) {
            throw new IllegalArgumentException(
                    field + " must be nullable bounded text");
        }
        return value.asText();
    }

    private static int requiredInteger(
            JsonNode parent,
            String field,
            int minimum,
            int maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isIntegralNumber()
                || value.asInt() < minimum || value.asInt() > maximum) {
            throw new IllegalArgumentException(
                    field + " must be a bounded integer");
        }
        return value.asInt();
    }

    private static LocalDateTime timestamp(JsonNode parent, String field) {
        try {
            return LocalDateTime.ofInstant(
                    Instant.parse(requiredText(parent, field, 40)),
                    ZoneOffset.UTC);
        } catch (RuntimeException invalidTimestamp) {
            throw new IllegalArgumentException(
                    field + " must be a UTC timestamp", invalidTimestamp);
        }
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message);
    }

    private static TargetApiException forbidden() {
        return new TargetApiException(
                403,
                "AUTH.CAPABILITY_REQUIRED",
                "仅平台管理员可以管理远程维护会话");
    }

    private static TargetApiException remoteSupportIdentityRequired() {
        return new TargetApiException(
                422,
                "DEVICE.REMOTE_SUPPORT_IDENTITY_REQUIRED",
                "设备尚未完成新版维护身份注册");
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "远程维护会话不存在");
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private record Asset(
            long id,
            String hardwareSn,
            String lifecycleStatus,
            String oneNetStatus,
            String tunnelPublicKey,
            String hostPublicKey) {
    }

    private record LockedAsset(
            long id,
            String hardwareSn,
            String lifecycleStatus,
            String oneNetStatus) {
    }

    private record MaintenanceIdentity(
            String tunnelPublicKey,
            String hostPublicKey) {
    }

    private record ReservedSession(UUID sessionUid, int port) {
    }

    private record RemoteSession(
            long id,
            UUID sessionUid,
            UUID operationUid,
            byte[] requestSha256,
            long assetId,
            long requestedByPlatformAdminId,
            UUID requestedByPlatformAdminUid,
            UUID maintenanceSshKeyUid,
            String state,
            int port,
            String deviceReportedState,
            UUID openCommandUid,
            UUID closeOperationUid,
            byte[] closeRequestSha256,
            UUID closeCommandUid,
            String failureCode,
            String certificate,
            LocalDateTime connectDeadlineAt,
            LocalDateTime expiresAt,
            LocalDateTime openedAt,
            LocalDateTime closedAt,
            LocalDateTime leaseReleasedAt,
            LocalDateTime createdAt,
            long version,
            String hardwareSn,
            String tunnelPublicKey,
            String tunnelFingerprint,
            String hostPublicKey) {
    }
}
