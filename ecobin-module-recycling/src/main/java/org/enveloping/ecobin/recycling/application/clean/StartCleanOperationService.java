package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.device.api.port.DeviceCommandCanonicalizationPort;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOperationAccepted;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.PreparedStatementCreator;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * 原子创建正常清运操作、新袋预留、设备独占和唯一开始命令。
 * 该事务只表示“清运已排队”，不会提前创建清运记录。
 */
@Service
public class StartCleanOperationService {

    private static final String TASK_TYPE = "START_CLEAN_OPERATION";
    private static final String TARGET_TYPE = "CLEAN_OPERATION";
    private static final String ACTION = "clean.start";
    private static final Duration START_WINDOW = Duration.ofSeconds(60);
    private static final Duration MAX_TRUSTED_RUNTIME_AGE =
            Duration.ofDays(1);
    private static final long RECOMMENDED_POLL_AFTER_MS = 1_000L;

    private final JdbcTemplate jdbc;
    private final StartCleanIdentityParticipationPort identity;
    private final ReliableDeviceTaskRegistrationPort taskRegistration;
    private final DeviceCommandTaskRefFactory taskRefFactory;
    private final DeviceCommandCanonicalizationPort canonicalizer;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;

    public StartCleanOperationService(
            JdbcTemplate jdbc,
            StartCleanIdentityParticipationPort identity,
            ReliableDeviceTaskRegistrationPort taskRegistration,
            DeviceCommandTaskRefFactory taskRefFactory,
            DeviceCommandCanonicalizationPort canonicalizer,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.identity = identity;
        this.taskRegistration = taskRegistration;
        this.taskRefFactory = taskRefFactory;
        this.canonicalizer = canonicalizer;
        this.audit = audit;
        this.objectMapper = objectMapper;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public CleanOperationAccepted start(
            UUID idempotencyKey,
            String deploymentCode,
            int portNo,
            String installedBagQr) {
        requireUuidV4(idempotencyKey);
        String normalizedDeployment = deploymentCode(deploymentCode);
        int normalizedPort = portNo(portNo);
        String normalizedBag = bagCode(installedBagQr);

        LockedMiniappCleanScope scope =
                identity.lockCurrentMiniappScope();
        CleanRule cleanRule = scope.organizationScopeRef()
                .withOrganizationScopeOnce(
                        this::lockCurrentCleanRule);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(scope);
        String fingerprint = fingerprint(
                cleaner.organizationUserUid().value(),
                normalizedDeployment,
                normalizedPort,
                normalizedBag);

        return cleaner.organizationUserRef()
                .withOrganizationUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId) -> startOrReplay(
                                idempotencyKey,
                                normalizedDeployment,
                                normalizedPort,
                                normalizedBag,
                                fingerprint,
                                cleanRule,
                                cleaner,
                                tenantId,
                                organizationId,
                                organizationUserId));
    }

    private CleanOperationAccepted startOrReplay(
            UUID idempotencyKey,
            String deploymentCode,
            int portNo,
            String installedBagQr,
            String fingerprint,
            CleanRule cleanRule,
            LockedCleanOrganizationUser cleaner,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        requireScope(cleanRule, tenantId, organizationId);
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(idempotencyKey);
        if (previous.isPresent()) {
            return replay(
                    previous.orElseThrow(),
                    fingerprint,
                    tenantId,
                    organizationId,
                    organizationUserId);
        }

        CreatedOperation created = create(
                idempotencyKey,
                deploymentCode,
                portNo,
                installedBagQr,
                cleanRule,
                tenantId,
                organizationId,
                organizationUserId);
        CleanOperationAccepted response = new CleanOperationAccepted(
                created.operationUid(),
                created.operationUid(),
                created.operationUid(),
                "PREPARED",
                0,
                portNo,
                installedBagQr,
                instant(created.authorizationExpiresAt()),
                "/api/v1/miniapp/clean-operations/"
                        + created.operationUid(),
                RECOMMENDED_POLL_AFTER_MS,
                List.of("WAIT"));

        Map<String, Object> safeSummary = new LinkedHashMap<>();
        safeSummary.put("fingerprint", fingerprint);
        safeSummary.put("response", response);
        try {
            cleaner.auditActorRef().withAuditActorOnce(
                    (auditTenantId,
                     auditOrganizationId,
                     auditOrganizationUserId) -> {
                        requireScope(
                                auditTenantId,
                                auditOrganizationId,
                                tenantId,
                                organizationId);
                        if (auditOrganizationUserId
                                != organizationUserId) {
                            throw new IllegalStateException(
                                    "clean audit actor changed identity");
                        }
                        audit.append(new AuditEntry(
                                UUID.randomUUID(),
                                UUID.randomUUID(),
                                idempotencyKey,
                                AuditScopeKind.ORGANIZATION,
                                tenantId,
                                organizationId,
                                AuditActorKind.ORGANIZATION_USER,
                                null,
                                null,
                                organizationUserId,
                                null,
                                null,
                                ACTION,
                                TARGET_TYPE,
                                created.operationUid().toString(),
                                "MINIAPP_USER",
                                "SUCCEEDED",
                                cleaner.loginSessionUid().value(),
                                null,
                                writeJson(safeSummary),
                                Instant.now()));
                        return null;
                    });
        } catch (DuplicateKeyException exception) {
            throw idempotencyConflict();
        }
        return response;
    }

    private CreatedOperation create(
            UUID correlationUid,
            String deploymentCode,
            int portNo,
            String installedBagQr,
            CleanRule cleanRule,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        long assetId = query("""
                        SELECT asset_id
                        FROM dev_device_deployment
                        WHERE public_code = ?
                        """,
                (rs, ignored) -> rs.getLong("asset_id"),
                deploymentCode)
                .stream()
                .findFirst()
                .orElseThrow(StartCleanOperationService::notFound);
        Asset asset = one("""
                        SELECT id, hardware_sn, lifecycle_status
                        FROM dev_device_asset
                        WHERE id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        rs.getString("hardware_sn"),
                        rs.getString("lifecycle_status")),
                assetId).orElseThrow(StartCleanOperationService::notFound);
        ActiveDeployment active = one("""
                        SELECT tenant_id, organization_id, deployment_id
                        FROM dev_asset_active_deployment
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ActiveDeployment(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("deployment_id")),
                asset.id()).orElseThrow(StartCleanOperationService::notFound);
        Deployment deployment = one("""
                        SELECT id, tenant_id, organization_id, asset_id,
                               public_code, lifecycle_status,
                               business_enabled
                        FROM dev_device_deployment
                        WHERE id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Deployment(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("asset_id"),
                        rs.getString("public_code"),
                        rs.getString("lifecycle_status"),
                        rs.getBoolean("business_enabled")),
                active.deploymentId())
                .orElseThrow(StartCleanOperationService::notFound);
        requireDeployment(
                asset,
                active,
                deployment,
                tenantId,
                organizationId,
                deploymentCode);

        DeploymentRuntime runtime = one("""
                        SELECT edge_connection_status, safety_status,
                               local_storage_health, local_storage_state,
                               trusted_runtime_edge_event_id,
                               trusted_runtime_edge_event_type,
                               trusted_runtime_sequence,
                               trusted_runtime_received_at,
                               orange_pi_reported_config_version_no,
                               orange_pi_reported_config_content_sha256,
                               orange_pi_reported_config_mcu_payload_sha256
                        FROM dev_deployment_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new DeploymentRuntime(
                        rs.getString("edge_connection_status"),
                        rs.getString("safety_status"),
                        rs.getString("local_storage_health"),
                        rs.getString("local_storage_state"),
                        nullableLong(
                                rs,
                                "trusted_runtime_edge_event_id"),
                        rs.getString(
                                "trusted_runtime_edge_event_type"),
                        nullableLong(
                                rs,
                                "trusted_runtime_sequence"),
                        rs.getObject(
                                "trusted_runtime_received_at",
                                LocalDateTime.class),
                        nullableLong(
                                rs,
                                "orange_pi_reported_config_version_no"),
                        rs.getBytes(
                                "orange_pi_reported_config_content_sha256"),
                        rs.getBytes(
                                "orange_pi_reported_config_mcu_payload_sha256")),
                tenantId,
                organizationId,
                deployment.id()).orElseThrow(
                StartCleanOperationService::cleaningUnavailable);

        if (!query("""
                        SELECT occupancy_kind
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getString("occupancy_kind"),
                asset.id()).isEmpty()) {
            throw new TargetApiException(
                    409,
                    "DEVICE.DEVICE_BUSY",
                    "设备正在执行其他物理操作");
        }

        Configuration configuration = one("""
                        SELECT id, version_no, content_sha256,
                               mcu_payload_sha256,
                               edge_heartbeat_interval_ms,
                               edge_heartbeat_miss_threshold
                        FROM dev_config_version
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                        ORDER BY version_no DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Configuration(
                        rs.getLong("id"),
                        rs.getLong("version_no"),
                        rs.getBytes("content_sha256"),
                        rs.getBytes("mcu_payload_sha256"),
                        rs.getLong("edge_heartbeat_interval_ms"),
                        rs.getLong("edge_heartbeat_miss_threshold")),
                tenantId,
                organizationId,
                deployment.id()).orElseThrow(
                StartCleanOperationService::configurationUnavailable);
        ConfigurationApplication application = one("""
                        SELECT status, reported_version_no,
                               reported_content_sha256,
                               reported_mcu_payload_sha256,
                               applied_at
                        FROM dev_config_application
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND config_version_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ConfigurationApplication(
                        rs.getString("status"),
                        nullableLong(rs, "reported_version_no"),
                        rs.getBytes("reported_content_sha256"),
                        rs.getBytes("reported_mcu_payload_sha256"),
                        rs.getObject(
                                "applied_at",
                                LocalDateTime.class)),
                tenantId,
                organizationId,
                deployment.id(),
                configuration.id()).orElseThrow(
                StartCleanOperationService::configurationUnavailable);

        Port port = one("""
                        SELECT id, port_no
                        FROM dev_port
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_no = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Port(
                        rs.getLong("id"),
                        rs.getInt("port_no")),
                tenantId,
                organizationId,
                deployment.id(),
                portNo).orElseThrow(StartCleanOperationService::notFound);
        PortConfiguration portConfiguration = one("""
                        SELECT id, business_enabled, calibration_version
                        FROM dev_port_config_snapshot
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND config_version_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PortConfiguration(
                        rs.getLong("id"),
                        rs.getBoolean("business_enabled"),
                        rs.getLong("calibration_version")),
                tenantId,
                organizationId,
                deployment.id(),
                configuration.id(),
                port.id()).orElseThrow(
                StartCleanOperationService::configurationUnavailable);
        PortRuntime portRuntime = one("""
                        SELECT delivery_door_actuator_health,
                               clean_lock_power_state,
                               clean_solenoid_health,
                               weight_sensor_health,
                               weight_measurement_status,
                               weight_value_available,
                               reported_weight_grams,
                               weight_value_kind,
                               calibration_version,
                               smoke_state,
                               smoke_sensor_health,
                               runtime_fault_bitmap,
                               safety_status,
                               pending_delivery_result_session_id,
                               trusted_runtime_edge_event_id,
                               trusted_runtime_edge_event_type,
                               trusted_runtime_sequence
                        FROM dev_port_runtime_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new PortRuntime(
                        rs.getString(
                                "delivery_door_actuator_health"),
                        rs.getString("clean_lock_power_state"),
                        rs.getString("clean_solenoid_health"),
                        rs.getString("weight_sensor_health"),
                        rs.getString("weight_measurement_status"),
                        nullableBoolean(rs, "weight_value_available"),
                        nullableLong(rs, "reported_weight_grams"),
                        rs.getString("weight_value_kind"),
                        nullableLong(rs, "calibration_version"),
                        rs.getString("smoke_state"),
                        rs.getString("smoke_sensor_health"),
                        nullableLong(rs, "runtime_fault_bitmap"),
                        rs.getString("safety_status"),
                        nullableLong(
                                rs,
                                "pending_delivery_result_session_id"),
                        nullableLong(
                                rs,
                                "trusted_runtime_edge_event_id"),
                        rs.getString(
                                "trusted_runtime_edge_event_type"),
                        nullableLong(
                                rs,
                                "trusted_runtime_sequence")),
                tenantId,
                organizationId,
                deployment.id(),
                port.id()).orElseThrow(
                StartCleanOperationService::cleaningUnavailable);

        LocalDateTime now = databaseNow();
        requireRuntime(
                runtime,
                application,
                configuration,
                portConfiguration,
                portRuntime,
                now);
        requireNoConflictingPortWork(
                tenantId,
                organizationId,
                port.id());

        CurrentBag oldBag = one("""
                        SELECT occupancy.bag_id,
                               bag.bag_uid,
                               bag.bag_code
                        FROM rec_bag_current_occupancy occupancy
                        JOIN rec_bag bag
                          ON bag.tenant_id = occupancy.tenant_id
                         AND bag.organization_id = occupancy.organization_id
                         AND bag.id = occupancy.bag_id
                        WHERE occupancy.tenant_id = ?
                          AND occupancy.organization_id = ?
                          AND occupancy.port_id = ?
                          AND occupancy.occupancy_type = 'PORT_BOUND'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CurrentBag(
                        rs.getLong("bag_id"),
                        UUID.fromString(rs.getString("bag_uid")),
                        rs.getString("bag_code")),
                tenantId,
                organizationId,
                port.id()).orElse(null);
        Capacity capacity = one("""
                        SELECT baseline_state, current_baseline_id,
                               current_baseline_weight_g
                        FROM rec_port_capacity_state
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Capacity(
                        rs.getString("baseline_state"),
                        nullableLong(rs, "current_baseline_id"),
                        nullableLong(
                                rs,
                                "current_baseline_weight_g")),
                tenantId,
                organizationId,
                port.id()).orElse(new Capacity(
                "UNINITIALIZED", null, null));

        Bag newBag = lockOrRegisterBag(
                tenantId,
                organizationId,
                installedBagQr,
                now);
        if (oldBag != null && oldBag.id() == newBag.id()) {
            throw new TargetApiException(
                    409,
                    "CLEAN.NEW_BAG_EQUALS_OLD_BAG",
                    "换入袋不能与当前投口袋相同");
        }
        requireBagUnoccupied(newBag.id());

        UUID operationUid = UUID.randomUUID();
        UUID commandUid = UUID.randomUUID();
        LocalDateTime authorizationExpiresAt =
                now.plus(START_WINDOW);
        Baseline frozenBaseline = baseline(oldBag, capacity);
        long operationId = insertOperation(
                operationUid,
                tenantId,
                organizationId,
                deployment.id(),
                port.id(),
                organizationUserId,
                configuration.id(),
                cleanRule,
                oldBag,
                frozenBaseline,
                newBag,
                portRuntime.pendingDeliveryResultSessionId(),
                authorizationExpiresAt,
                now);
        reserveBag(
                newBag,
                tenantId,
                organizationId,
                port.id(),
                operationId,
                now);
        insertPhotoSlots(
                tenantId,
                organizationId,
                operationId,
                now);
        insertDeviceOccupancy(
                asset.id(),
                tenantId,
                organizationId,
                deployment.id(),
                operationId,
                now);

        Map<String, Object> payload = commandPayload(
                operationUid,
                portNo,
                oldBag,
                frozenBaseline,
                newBag,
                configuration,
                cleanRule.timeoutSeconds());
        byte[] payloadSha256 = canonicalizer.payloadSha256(payload);
        Map<String, Object> envelope = commandEnvelope(
                commandUid,
                deploymentCode,
                operationUid,
                now,
                authorizationExpiresAt,
                payload,
                payloadSha256);
        byte[] envelopeSha256 = canonicalizer.payloadSha256(envelope);
        long commandId = insertCommand(
                commandUid,
                tenantId,
                organizationId,
                deployment.id(),
                operationId,
                writeJson(envelope),
                envelopeSha256,
                now);
        registerTask(
                correlationUid,
                operationUid,
                commandUid,
                deployment,
                asset,
                commandId,
                envelopeSha256);
        return new CreatedOperation(
                operationUid,
                authorizationExpiresAt);
    }

    private CleanRule lockCurrentCleanRule(
            long tenantId,
            long organizationId) {
        CleanRuleHead head = one("""
                        SELECT current_config_id, current_version_no
                        FROM rec_organization_clean_config_head
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CleanRuleHead(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no")),
                tenantId,
                organizationId).orElseThrow(
                StartCleanOperationService::cleanConfigurationUnavailable);
        return one("""
                        SELECT id, version_no, content_sha256,
                               operation_timeout_seconds
                        FROM rec_organization_clean_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                          AND version_no = ?
                        """,
                (rs, ignored) -> new CleanRule(
                        tenantId,
                        organizationId,
                        rs.getLong("id"),
                        rs.getLong("version_no"),
                        rs.getBytes("content_sha256"),
                        rs.getInt("operation_timeout_seconds")),
                tenantId,
                organizationId,
                head.id(),
                head.version()).orElseThrow(
                StartCleanOperationService::cleanConfigurationUnavailable);
    }

    private void requireNoConflictingPortWork(
            long tenantId,
            long organizationId,
            long portId) {
        if (!query("""
                        SELECT id
                        FROM rec_clean_operation
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status IN (
                              'PREPARED', 'EDGE_SAVED', 'IN_PROGRESS',
                              'RECOVERY_REQUIRED'
                          )
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                portId).isEmpty()) {
            throw new TargetApiException(
                    409,
                    "CLEAN.PORT_OPERATION_ACTIVE",
                    "当前投口已有未结束清运操作");
        }
        boolean fullnessBusy = !query("""
                        SELECT id
                        FROM rec_fullness_detection
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status IN (
                              'PENDING_INITIAL_SAMPLE',
                              'WAITING_RECHECK'
                          )
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                portId).isEmpty();
        boolean baselineBusy = !query("""
                        SELECT id
                        FROM rec_port_baseline_measurement
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND port_id = ?
                          AND status = 'PENDING'
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("id"),
                tenantId,
                organizationId,
                portId).isEmpty();
        if (fullnessBusy || baselineBusy) {
            throw new TargetApiException(
                    409,
                    "DEVICE.PORT_WORK_ACTIVE",
                    "当前投口正在执行检测或基准重测，请稍后重试");
        }
    }

    private Bag lockOrRegisterBag(
            long tenantId,
            long organizationId,
            String bagCode,
            LocalDateTime now) {
        Optional<Bag> existing = one("""
                        SELECT id, tenant_id, organization_id,
                               bag_uid, bag_code
                        FROM rec_bag
                        WHERE bag_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Bag(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        UUID.fromString(rs.getString("bag_uid")),
                        rs.getString("bag_code")),
                bagCode);
        if (existing.isPresent()) {
            Bag bag = existing.orElseThrow();
            if (bag.tenantId() != tenantId
                    || bag.organizationId() != organizationId) {
                throw bagUnavailable();
            }
            return bag;
        }
        UUID bagUid = UUID.randomUUID();
        long id;
        try {
            id = insertAndReturnKey("""
                            INSERT INTO rec_bag (
                                tenant_id, organization_id,
                                bag_uid, bag_code,
                                registered_at, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                    tenantId,
                    organizationId,
                    bagUid.toString(),
                    bagCode,
                    now,
                    now);
        } catch (DuplicateKeyException exception) {
            throw bagUnavailable();
        }
        return new Bag(
                id,
                tenantId,
                organizationId,
                bagUid,
                bagCode);
    }

    private void requireBagUnoccupied(long bagId) {
        if (!query("""
                        SELECT occupancy_type
                        FROM rec_bag_current_occupancy
                        WHERE bag_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getString("occupancy_type"),
                bagId).isEmpty()) {
            throw bagUnavailable();
        }
    }

    private long insertOperation(
            UUID operationUid,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId,
            long organizationUserId,
            long deviceConfigurationId,
            CleanRule cleanRule,
            CurrentBag oldBag,
            Baseline baseline,
            Bag newBag,
            Long pendingDeliverySessionId,
            LocalDateTime authorizationExpiresAt,
            LocalDateTime now) {
        return insertAndReturnKey("""
                INSERT INTO rec_clean_operation (
                    operation_uid, tenant_id, organization_id,
                    deployment_id, port_id,
                    cleaner_organization_user_id,
                    device_config_version_id,
                    clean_config_version_id,
                    clean_config_version_no,
                    operation_timeout_seconds,
                    old_bag_binding_state, old_bag_id,
                    old_bag_code_snapshot,
                    old_baseline_state, old_baseline_id,
                    old_baseline_weight_g,
                    pre_unlock_weight_status, pre_unlock_weight_g,
                    pre_unlock_weight_fault_code,
                    new_bag_id, new_bag_code_snapshot,
                    pending_delivery_result_session_id,
                    status,
                    edge_saved_confirmed,
                    first_unlock_may_have_executed,
                    clean_lock_deenergized_confirmed,
                    cleaner_physical_close_confirmed,
                    start_authorization_expires_at,
                    edge_saved_at, first_possible_unlock_at,
                    solenoid_powered_off_at,
                    cleaner_confirmed_closed_at,
                    execution_deadline_at,
                    pre_unlock_end_requested_at,
                    recovery_requested_at,
                    reopen_count, recovery_count,
                    completion_record_id, ended_at, end_reason,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    'PENDING', NULL, NULL,
                    ?, ?, ?,
                    'PREPARED', 0, 0, 0, 0,
                    ?, NULL, NULL, NULL, NULL, NULL,
                    NULL, NULL, 0, 0,
                    NULL, NULL, NULL, 0, ?, ?
                )
                """,
                operationUid.toString(),
                tenantId,
                organizationId,
                deploymentId,
                portId,
                organizationUserId,
                deviceConfigurationId,
                cleanRule.id(),
                cleanRule.version(),
                cleanRule.timeoutSeconds(),
                oldBag == null ? "MISSING" : "BOUND",
                oldBag == null ? null : oldBag.id(),
                oldBag == null ? null : oldBag.bagCode(),
                baseline.state(),
                baseline.id(),
                baseline.weightGrams(),
                newBag.id(),
                newBag.bagCode(),
                pendingDeliverySessionId,
                authorizationExpiresAt,
                now,
                now);
    }

    private void reserveBag(
            Bag bag,
            long tenantId,
            long organizationId,
            long portId,
            long operationId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        INSERT INTO rec_bag_current_occupancy (
                            bag_id, tenant_id, organization_id,
                            occupancy_type, port_id,
                            clean_operation_id, acquired_at
                        ) VALUES (
                            ?, ?, ?, 'CLEAN_RESERVED', NULL, ?, ?
                        )
                        """,
                bag.id(),
                tenantId,
                organizationId,
                operationId,
                now), "reserve clean bag");
        requireSingle(jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            'RESERVED_FOR_CLEAN', ?, ?
                        )
                        """,
                UUID.randomUUID().toString(),
                tenantId,
                organizationId,
                bag.id(),
                portId,
                operationId,
                now,
                now), "record clean bag reservation");
    }

    private void insertPhotoSlots(
            long tenantId,
            long organizationId,
            long operationId,
            LocalDateTime now) {
        for (String position : List.of(
                "FIRST_OPEN_INNER",
                "FIRST_OPEN_OUTER",
                "FINAL_CLOSE_INNER",
                "FINAL_CLOSE_OUTER")) {
            requireSingle(jdbc.update("""
                            INSERT INTO rec_clean_photo (
                                tenant_id, organization_id,
                                clean_operation_id, position,
                                photo_uid, status, object_url,
                                sha256, size_bytes, captured_at,
                                linked_at, missing_reason,
                                created_at, updated_at
                            ) VALUES (
                                ?, ?, ?, ?, NULL, 'UPLOAD_PENDING',
                                NULL, NULL, NULL, NULL, NULL, 'UPLOAD_PENDING',
                                ?, ?
                            )
                            """,
                    tenantId,
                    organizationId,
                    operationId,
                    position,
                    now,
                    now), "create clean photo slot");
        }
    }

    private void insertDeviceOccupancy(
            long assetId,
            long tenantId,
            long organizationId,
            long deploymentId,
            long operationId,
            LocalDateTime now) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_device_occupancy (
                            asset_id, tenant_id, organization_id,
                            deployment_id, occupancy_kind,
                            delivery_session_id, clean_operation_id,
                            acquired_at, lock_version
                        ) VALUES (
                            ?, ?, ?, ?, 'CLEAN', NULL, ?, ?, 0
                        )
                        """,
                assetId,
                tenantId,
                organizationId,
                deploymentId,
                operationId,
                now), "acquire clean device occupancy");
    }

    private long insertCommand(
            UUID commandUid,
            long tenantId,
            long organizationId,
            long deploymentId,
            long operationId,
            String envelopeJson,
            byte[] envelopeSha256,
            LocalDateTime now) {
        return insertAndReturnKey("""
                INSERT INTO dev_device_command (
                    command_uid, tenant_id, organization_id,
                    deployment_id, command_type,
                    delivery_session_id, clean_operation_id,
                    config_application_id, fullness_detection_id,
                    baseline_measurement_id,
                    payload_schema_version, semantic_payload,
                    semantic_payload_sha256, physical_state,
                    queued_at, edge_accepted_at,
                    physical_started_at, physical_ended_at,
                    lock_version, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'START_CLEAN_OPERATION',
                    NULL, ?, NULL, NULL, NULL,
                    1, CAST(? AS JSON), ?, 'QUEUED',
                    ?, NULL, NULL, NULL, 0, ?, ?
                )
                """,
                commandUid.toString(),
                tenantId,
                organizationId,
                deploymentId,
                operationId,
                envelopeJson,
                envelopeSha256,
                now,
                now,
                now);
    }

    private void registerTask(
            UUID correlationUid,
            UUID operationUid,
            UUID commandUid,
            Deployment deployment,
            Asset asset,
            long commandId,
            byte[] envelopeSha256) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("schemaVersion", 1);
        snapshot.put("commandUid", commandUid.toString());
        snapshot.put("commandType", TASK_TYPE);
        snapshot.put("hardwareSn", asset.hardwareSn());
        snapshot.put("deploymentCode", deployment.publicCode());
        snapshot.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", operationUid.toString()));
        snapshot.put("payloadSchemaVersion", 1);
        snapshot.put(
                "semanticPayloadSha256",
                canonicalizer.hex(envelopeSha256));
        taskRegistration.register(new ReliableDeviceTaskRegistration(
                TASK_TYPE,
                TASK_TYPE + ":"
                        + operationUid.toString()
                        .toUpperCase(Locale.ROOT),
                TARGET_TYPE,
                operationUid.toString(),
                taskRefFactory.issue(
                        deployment.tenantId(),
                        deployment.organizationId(),
                        deployment.id(),
                        commandId),
                1,
                writeJson(snapshot),
                envelopeSha256,
                correlationUid,
                null,
                1,
                false,
                null));
    }

    private Map<String, Object> commandPayload(
            UUID operationUid,
            int portNo,
            CurrentBag oldBag,
            Baseline baseline,
            Bag newBag,
            Configuration configuration,
            int timeoutSeconds) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("version", configuration.version());
        config.put(
                "contentSha256",
                canonicalizer.hex(configuration.contentSha256()));
        config.put(
                "mcuPayloadSha256",
                canonicalizer.hex(configuration.mcuPayloadSha256()));

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("operationUid", operationUid.toString());
        payload.put("portNo", portNo);
        payload.put(
                "oldBagUid",
                oldBag == null ? null : oldBag.bagUid().toString());
        payload.put(
                "oldBaselineWeightGrams",
                baseline.weightGrams());
        payload.put("newBagUid", newBag.bagUid().toString());
        payload.put("config", config);
        payload.put(
                "operationWindowMs",
                Math.multiplyExact((long) timeoutSeconds, 1_000L));
        payload.put("recoveryGeneration", 0);
        return payload;
    }

    private Map<String, Object> commandEnvelope(
            UUID commandUid,
            String deploymentCode,
            UUID operationUid,
            LocalDateTime issuedAt,
            LocalDateTime expiresAt,
            Map<String, Object> payload,
            byte[] payloadSha256) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schemaVersion", 1);
        envelope.put("commandUid", commandUid.toString());
        envelope.put("commandType", TASK_TYPE);
        envelope.put("deploymentCode", deploymentCode);
        envelope.put("target", Map.of(
                "type", TARGET_TYPE,
                "uid", operationUid.toString()));
        envelope.put("issuedAt", instant(issuedAt).toString());
        envelope.put("expiresAt", instant(expiresAt).toString());
        envelope.put("payloadSchemaVersion", 1);
        envelope.put(
                "payloadSha256",
                canonicalizer.hex(payloadSha256));
        envelope.put("payload", payload);
        envelope.put("cosGrant", null);
        return envelope;
    }

    private static Baseline baseline(
            CurrentBag oldBag,
            Capacity capacity) {
        if (oldBag == null) {
            return new Baseline("MISSING", null, null);
        }
        if ("VALID".equals(capacity.state())
                && capacity.id() != null
                && capacity.weightGrams() != null) {
            return new Baseline(
                    "TRUSTED",
                    capacity.id(),
                    capacity.weightGrams());
        }
        return new Baseline("UNTRUSTED", null, null);
    }

    private static void requireRuntime(
            DeploymentRuntime runtime,
            ConfigurationApplication application,
            Configuration configuration,
            PortConfiguration portConfiguration,
            PortRuntime portRuntime,
            LocalDateTime now) {
        boolean configurationApplied = "APPLIED".equals(
                application.status())
                && application.appliedAt() != null
                && Objects.equals(
                application.reportedVersion(),
                configuration.version())
                && digestEquals(
                application.reportedContentSha256(),
                configuration.contentSha256())
                && digestEquals(
                application.reportedMcuPayloadSha256(),
                configuration.mcuPayloadSha256())
                && Objects.equals(
                runtime.reportedVersion(),
                configuration.version())
                && digestEquals(
                runtime.reportedContentSha256(),
                configuration.contentSha256())
                && digestEquals(
                runtime.reportedMcuPayloadSha256(),
                configuration.mcuPayloadSha256());
        if (!configurationApplied) {
            throw configurationUnavailable();
        }
        boolean runtimeTrusted = runtime.trustedEventId() != null
                && "DEVICE_RUNTIME_SNAPSHOT".equals(
                runtime.trustedEventType())
                && runtime.trustedSequence() != null
                && runtime.trustedSequence() > 0
                && "ONLINE".equals(
                runtime.edgeConnectionStatus())
                && "SAFE".equals(runtime.safetyStatus())
                && "OK".equals(runtime.localStorageHealth())
                && "HEALTHY".equals(runtime.localStorageState())
                && runtime.receivedAt() != null
                && runtimeFresh(
                runtime.receivedAt(),
                now,
                configuration.heartbeatIntervalMs(),
                configuration.heartbeatMissThreshold());
        boolean sameTrustedSnapshot =
                portRuntime.trustedEventId() != null
                        && "DEVICE_RUNTIME_SNAPSHOT".equals(
                        portRuntime.trustedEventType())
                        && Objects.equals(
                        portRuntime.trustedEventId(),
                        runtime.trustedEventId())
                        && Objects.equals(
                        portRuntime.trustedSequence(),
                        runtime.trustedSequence());
        boolean portTrusted = sameTrustedSnapshot
                && portConfiguration.businessEnabled()
                && "DEENERGIZED".equals(
                portRuntime.cleanLockPowerState())
                && "OK".equals(portRuntime.cleanSolenoidHealth())
                && "OK".equals(portRuntime.weightSensorHealth())
                && "STABLE".equals(
                portRuntime.weightMeasurementStatus())
                && Boolean.TRUE.equals(
                portRuntime.weightValueAvailable())
                && portRuntime.reportedWeightGrams() != null
                && "STABLE_WINDOW_MEAN".equals(
                portRuntime.weightValueKind())
                && Objects.equals(
                portRuntime.calibrationVersion(),
                portConfiguration.calibrationVersion())
                && "NORMAL".equals(portRuntime.smokeState())
                && "OK".equals(portRuntime.smokeSensorHealth())
                && Objects.equals(portRuntime.faultBitmap(), 0L)
                && "SAFE".equals(portRuntime.safetyStatus());
        if (!runtimeTrusted || !portTrusted) {
            throw cleaningUnavailable();
        }
    }

    private static void requireDeployment(
            Asset asset,
            ActiveDeployment active,
            Deployment deployment,
            long tenantId,
            long organizationId,
            String deploymentCode) {
        boolean valid = asset.id() == deployment.assetId()
                && active.tenantId() == tenantId
                && active.organizationId() == organizationId
                && deployment.tenantId() == tenantId
                && deployment.organizationId() == organizationId
                && deployment.publicCode().equals(deploymentCode)
                && "IN_USE".equals(asset.lifecycleStatus())
                && "ENABLED".equals(deployment.lifecycleStatus())
                && deployment.businessEnabled();
        if (!valid) {
            throw notFound();
        }
    }

    private CleanOperationAccepted replay(
            SuccessfulAudit previous,
            String fingerprint,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        JsonNode summary = readJson(previous.safeChangeSummaryJson());
        if (previous.actorKind()
                != AuditActorKind.ORGANIZATION_USER
                || !Objects.equals(
                previous.organizationUserId(),
                organizationUserId)
                || previous.scopeKind()
                != AuditScopeKind.ORGANIZATION
                || !Objects.equals(previous.tenantId(), tenantId)
                || !Objects.equals(
                previous.organizationId(),
                organizationId)
                || !ACTION.equals(previous.actionCode())
                || !TARGET_TYPE.equals(previous.targetType())
                || !fingerprint.equals(
                summary.path("fingerprint").asText())) {
            throw idempotencyConflict();
        }
        try {
            CleanOperationAccepted response = objectMapper.treeToValue(
                    summary.path("response"),
                    CleanOperationAccepted.class);
            if (response == null
                    || !response.operationUid().toString().equals(
                    previous.targetStableKey())) {
                throw idempotencyConflict();
            }
            return response;
        } catch (TargetApiException exception) {
            throw exception;
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private LocalDateTime databaseNow() {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
        if (now == null) {
            throw new IllegalStateException(
                    "database time is unavailable");
        }
        return now;
    }

    private long insertAndReturnKey(String sql, Object... parameters) {
        KeyHolder keys = new GeneratedKeyHolder();
        PreparedStatementCreator creator = connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    sql,
                    Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < parameters.length; index++) {
                statement.setObject(index + 1, parameters[index]);
            }
            return statement;
        };
        requireSingle(
                jdbc.update(creator, keys),
                "insert clean fact");
        Number key = keys.getKey();
        if (key == null || key.longValue() <= 0) {
            throw new IllegalStateException(
                    "clean generated key is unavailable");
        }
        return key.longValue();
    }

    private <T> Optional<T> one(
            String sql,
            org.springframework.jdbc.core.RowMapper<T> mapper,
            Object... parameters) {
        return query(sql, mapper, parameters).stream().findFirst();
    }

    private <T> List<T> query(
            String sql,
            org.springframework.jdbc.core.RowMapper<T> mapper,
            Object... parameters) {
        return jdbc.query(sql, mapper, parameters);
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "clean command JSON cannot be encoded",
                    exception);
        }
    }

    private JsonNode readJson(String value) {
        try {
            return objectMapper.readTree(value);
        } catch (Exception exception) {
            throw idempotencyConflict();
        }
    }

    private static String fingerprint(
            UUID userUid,
            String deploymentCode,
            int portNo,
            String bagCode) {
        String value = userUid + "\u0000" + deploymentCode
                + "\u0000" + portNo + "\u0000" + bagCode;
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(
                            value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        }
    }

    private static String deploymentCode(String value) {
        if (value == null) {
            throw notFound();
        }
        String normalized = value.trim();
        if (!normalized.matches("Dp_[A-Za-z0-9_-]{6,61}")) {
            throw notFound();
        }
        return normalized;
    }

    private static String bagCode(String value) {
        if (value == null) {
            throw new TargetApiException(
                    400,
                    "CLEAN.BAG_CODE_INVALID",
                    "换入袋码不能为空");
        }
        String normalized = value.trim();
        if (!normalized.matches("[A-Za-z0-9_-]{8,64}")) {
            throw new TargetApiException(
                    400,
                    "CLEAN.BAG_CODE_INVALID",
                    "换入袋码格式无效");
        }
        return normalized;
    }

    private static int portNo(int value) {
        if (value < 1 || value > 6) {
            throw notFound();
        }
        return value;
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4
                || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static void requireScope(
            CleanRule rule,
            long tenantId,
            long organizationId) {
        requireScope(
                rule.tenantId(),
                rule.organizationId(),
                tenantId,
                organizationId);
    }

    private static void requireScope(
            long actualTenant,
            long actualOrganization,
            long tenantId,
            long organizationId) {
        if (actualTenant != tenantId
                || actualOrganization != organizationId) {
            throw new IllegalStateException(
                    "clean transaction scope changed after locking");
        }
    }

    private static void requireSingle(int affected, String operation) {
        if (affected != 1) {
            throw new IllegalStateException(
                    operation + " affected " + affected + " rows");
        }
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static boolean digestEquals(byte[] left, byte[] right) {
        return left != null
                && right != null
                && MessageDigest.isEqual(left, right);
    }

    private static boolean runtimeFresh(
            LocalDateTime receivedAt,
            LocalDateTime now,
            long heartbeatIntervalMs,
            long heartbeatMissThreshold) {
        if (heartbeatIntervalMs <= 0
                || heartbeatMissThreshold <= 0
                || receivedAt.isAfter(now)) {
            return false;
        }
        long maximumMs = MAX_TRUSTED_RUNTIME_AGE.toMillis();
        long allowedMs = heartbeatIntervalMs >
                maximumMs / heartbeatMissThreshold
                ? maximumMs
                : Math.min(
                heartbeatIntervalMs * heartbeatMissThreshold,
                maximumMs);
        try {
            long ageMs = Duration.between(receivedAt, now).toMillis();
            return ageMs >= 0 && ageMs <= allowedMs;
        } catch (ArithmeticException exception) {
            return false;
        }
    }

    private static Boolean nullableBoolean(ResultSet rs, String column)
            throws SQLException {
        boolean value = rs.getBoolean(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant instant(LocalDateTime value) {
        return value.toInstant(ZoneOffset.UTC);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "资源不存在");
    }

    private static TargetApiException cleaningUnavailable() {
        return new TargetApiException(
                422,
                "DEVICE.CLEANING_UNAVAILABLE",
                "设备当前不具备安全清运条件");
    }

    private static TargetApiException configurationUnavailable() {
        return new TargetApiException(
                422,
                "DEVICE.CONFIGURATION_NOT_APPLIED",
                "设备当前配置尚未完整应用");
    }

    private static TargetApiException cleanConfigurationUnavailable() {
        return new TargetApiException(
                422,
                "CLEAN.CONFIGURATION_UNAVAILABLE",
                "机构还没有可用于清运的当前规则");
    }

    private static TargetApiException bagUnavailable() {
        return new TargetApiException(
                409,
                "CLEAN.BAG_UNAVAILABLE",
                "换入袋当前不可用于本次清运");
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于另一项清运请求");
    }

    private record CleanRuleHead(long id, long version) {
    }

    private record CleanRule(
            long tenantId,
            long organizationId,
            long id,
            long version,
            byte[] contentSha256,
            int timeoutSeconds) {
    }

    private record Asset(
            long id,
            String hardwareSn,
            String lifecycleStatus) {
    }

    private record ActiveDeployment(
            long tenantId,
            long organizationId,
            long deploymentId) {
    }

    private record Deployment(
            long id,
            long tenantId,
            long organizationId,
            long assetId,
            String publicCode,
            String lifecycleStatus,
            boolean businessEnabled) {
    }

    private record DeploymentRuntime(
            String edgeConnectionStatus,
            String safetyStatus,
            String localStorageHealth,
            String localStorageState,
            Long trustedEventId,
            String trustedEventType,
            Long trustedSequence,
            LocalDateTime receivedAt,
            Long reportedVersion,
            byte[] reportedContentSha256,
            byte[] reportedMcuPayloadSha256) {
    }

    private record Configuration(
            long id,
            long version,
            byte[] contentSha256,
            byte[] mcuPayloadSha256,
            long heartbeatIntervalMs,
            long heartbeatMissThreshold) {
    }

    private record ConfigurationApplication(
            String status,
            Long reportedVersion,
            byte[] reportedContentSha256,
            byte[] reportedMcuPayloadSha256,
            LocalDateTime appliedAt) {
    }

    private record Port(long id, int portNo) {
    }

    private record PortConfiguration(
            long id,
            boolean businessEnabled,
            long calibrationVersion) {
    }

    private record PortRuntime(
            String deliveryDoorActuatorHealth,
            String cleanLockPowerState,
            String cleanSolenoidHealth,
            String weightSensorHealth,
            String weightMeasurementStatus,
            Boolean weightValueAvailable,
            Long reportedWeightGrams,
            String weightValueKind,
            Long calibrationVersion,
            String smokeState,
            String smokeSensorHealth,
            Long faultBitmap,
            String safetyStatus,
            Long pendingDeliveryResultSessionId,
            Long trustedEventId,
            String trustedEventType,
            Long trustedSequence) {
    }

    private record CurrentBag(
            long id,
            UUID bagUid,
            String bagCode) {
    }

    private record Bag(
            long id,
            long tenantId,
            long organizationId,
            UUID bagUid,
            String bagCode) {
    }

    private record Capacity(
            String state,
            Long id,
            Long weightGrams) {
    }

    private record Baseline(
            String state,
            Long id,
            Long weightGrams) {
    }

    private record CreatedOperation(
            UUID operationUid,
            LocalDateTime authorizationExpiresAt) {
    }
}
