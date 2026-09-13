package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.port.CompleteDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.port.DeliveryCompletionBusinessWriter;
import org.enveloping.ecobin.device.api.result.DeliveryCompleteDoorCommand;
import org.enveloping.ecobin.device.api.result.DeliveryCompleteMeasurement;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhoto;
import org.enveloping.ecobin.device.api.result.DeliveryCompletePhysicalFact;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionBusinessResult;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskProofPort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxQuarantinePort;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * 保存可信香橙派上报的唯一正常投递结果，并在释放整机占位前调用 recycling 建单。
 *
 * <p>正常稳定均值或可用超时中位数沿原事务处理；真正缺值、会话中断和现场恢复不在这里猜测或补造。
 * eventUid 负责传输去重，sessionUid 才是“一次会话最多一单”的业务唯一根。</p>
 */
@Service
public class TrustedDeliveryCompletionService
        implements CompleteDeliveryDeviceParticipationPort {

    private static final String MESSAGE_KIND = "DELIVERY_COMPLETE";
    private static final String TASK_TYPE = "START_DELIVERY_SESSION";
    private static final String TARGET_TYPE = "DELIVERY_SESSION";

    static final String FIND_EDGE_COLLISIONS_SQL = """
            SELECT id, event_uid, asset_id,
                   edge_event_sequence,
                   LOWER(HEX(canonical_sha256))
                       AS canonical_sha256,
                   source_inbox_id
            FROM dev_edge_event
            WHERE event_uid = ?
               OR (
                    asset_id = ?
                    AND edge_event_sequence = ?
               )
               OR source_inbox_id = ?
            """;

    static final String LOCK_PORT_RUNTIME_SQL = """
            SELECT port_id
            FROM dev_port_runtime_state
            WHERE port_id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
            FOR UPDATE
            """;

    static final String LOAD_PORT_SQL = """
            SELECT id
            FROM dev_port
            WHERE id = ?
              AND tenant_id = ?
              AND organization_id = ?
              AND asset_id = ?
            """;

    static final String LOAD_PORT_CONFIGURATION_SQL = """
            SELECT port.port_no,
                   snapshot.fullness_mode,
                   snapshot.configured_full_weight_g,
                   snapshot.fullness_settle_wait_ms,
                   snapshot.fullness_confirmation_wait_ms,
                   snapshot.weight_measurement_timeout_ms,
                   snapshot.weight_required_sample_count,
                   snapshot.weight_minimum_g,
                   snapshot.weight_maximum_g,
                   snapshot.calibration_version
            FROM dev_port_config_snapshot snapshot
            JOIN dev_port port
              ON port.tenant_id = snapshot.tenant_id
             AND port.organization_id =
                 snapshot.organization_id
             AND port.asset_id =
                 snapshot.asset_id
             AND port.id = snapshot.port_id
            WHERE snapshot.id = ?
              AND snapshot.config_version_id = ?
              AND snapshot.port_id = ?
              AND snapshot.tenant_id = ?
              AND snapshot.organization_id = ?
              AND snapshot.asset_id = ?
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final DeliveryCompletionFactsRefFactory factsRefFactory;
    private final ReliableEdgeConfirmationService confirmationService;
    private final ReliableDeviceTaskProofPort taskProofPort;
    private final TrustedInboxQuarantinePort quarantinePort;
    private final TrustedOrganizationInboxRefFactory inboxRefFactory;

    public TrustedDeliveryCompletionService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            DeliveryCompletionFactsRefFactory factsRefFactory,
            ReliableEdgeConfirmationService confirmationService,
            ReliableDeviceTaskProofPort taskProofPort,
            TrustedInboxQuarantinePort quarantinePort,
            TrustedOrganizationInboxRefFactory inboxRefFactory) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.factsRefFactory = factsRefFactory;
        this.confirmationService = confirmationService;
        this.taskProofPort = taskProofPort;
        this.quarantinePort = quarantinePort;
        this.inboxRefFactory = inboxRefFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult complete(
            TrustedDeviceInboxEvent event,
            DeliveryCompletionBusinessWriter businessWriter) {
        if (!MESSAGE_KIND.equals(event.messageKind())
                || event.normalizedSchemaVersion()
                != TrustedDeviceInboxEvent.CURRENT_NORMALIZED_SCHEMA_VERSION) {
            throw new IllegalArgumentException(
                    "unsupported delivery completion inbox message");
        }
        DeliveryCompletePhysicalFact fact =
                parse(event.normalizedPayload());
        requireSupportedCompletion(fact);
        return event.sourceInbox().use(
                (inboxId, tenantId, organizationId) ->
                        completeWithinScope(
                                fact,
                                inboxId,
                                tenantId,
                                organizationId,
                                businessWriter));
    }

    private TrustedDeviceEventApplyResult completeWithinScope(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            DeliveryCompletionBusinessWriter businessWriter) {
        // 锁序从永久资产向本次作业逐层收窄：资产 → 运行态 → session → 投口 →
        // 整机占位 → 开始命令。完成既有作业不受资产后来禁用或报废影响。
        AssetRow asset = lockAsset(fact);
        if (asset.tenantId() != tenantId
                || asset.organizationId() != organizationId) {
            throw untrusted();
        }
        long assetId = asset.id();
        SessionRow session;
        CommandRow command;
        PortConfiguration portConfiguration;
        try {
            lockRuntime(assetId, tenantId, organizationId);
            session = lockSession(
                    fact,
                    assetId,
                    tenantId,
                    organizationId);
            if ("BUSINESS_CONFIRMED".equals(session.status())) {
                return requirePreviouslyApplied(
                        fact,
                        inboxId,
                        tenantId,
                        organizationId,
                        assetId,
                        session);
            }
            lockPortRuntimeAndRequirePort(
                    session.portId(),
                    assetId,
                    tenantId,
                    organizationId);
            lockOccupancy(
                    session.id(),
                    assetId,
                    tenantId,
                    organizationId);
            command = lockCommand(
                    fact,
                    session.id(),
                    assetId,
                    tenantId,
                    organizationId);
            portConfiguration = loadPortConfiguration(
                    session,
                    assetId,
                    tenantId,
                    organizationId);
            // 重新核对袋、价格、配置、命令和目标；设备载荷不能改变开始时冻结的业务事实。
            verifyFrozenFacts(fact, session, command, portConfiguration);

            List<ExistingEdge> collisions = findEdgeCollisions(
                    fact,
                    assetId,
                    inboxId);
            if (!collisions.isEmpty()) {
                return quarantine(
                        fact,
                        inboxId,
                        tenantId,
                        organizationId,
                        assetId,
                        "IDENTITY_CONTENT_CONFLICT",
                        "EVENT_IDENTITY_CONFLICT",
                        "delivery completion identity or sequence conflicts");
            }
        } catch (UntrustedInboxSourceException obsoleteTarget) {
            return quarantine(
                    fact,
                    inboxId,
                    tenantId,
                    organizationId,
                    assetId,
                    "EVENT_TARGET_NOT_AUTHORITATIVE",
                    "EVENT_TARGET_NOT_AUTHORITATIVE",
                    "delivery completion references an obsolete target");
        }

        LocalDateTime receivedAt = databaseNow();
        // 原始边缘事件和物理结果只追加、不覆盖。审核纠错只能创建业务修订，不能反写这里。
        long edgeEventId = insertEdgeEvent(
                fact,
                inboxId,
                tenantId,
                organizationId,
                assetId,
                receivedAt);
        long physicalResultId = insertPhysicalResult(
                fact,
                session,
                command,
                edgeEventId,
                tenantId,
                organizationId,
                assetId,
                receivedAt);

        DeliveryCompletionPersistenceFacts persistenceFacts =
                new DeliveryCompletionPersistenceFacts(
                        tenantId,
                        organizationId,
                        assetId,
                        session.portId(),
                        session.id(),
                        session.organizationUserId(),
                        command.id(),
                        edgeEventId,
                        physicalResultId,
                        session.deviceConfigVersionId(),
                        session.portConfigSnapshotId(),
                        session.deliveryConfigVersionId(),
                        session.deliveryConfigContentSha256(),
                        session.bagId(),
                        session.bagUid(),
                        session.bagCode(),
                        session.unitPrice(),
                        session.openBalanceFloorCent(),
                        session.maxReviewAbsWeightGrams(),
                        session.negativeWeightThresholdGrams(),
                        portConfiguration.fullnessMode(),
                        portConfiguration.configuredFullWeightGrams(),
                        portConfiguration.fullnessSettleWaitMs(),
                        portConfiguration.fullnessConfirmationWaitMs(),
                        portConfiguration.fullnessMeasurementTimeoutMs(),
                        receivedAt,
                        fact);
        // recycling 在当前 MANDATORY 事务中创建订单。订单、设备完成状态、占位释放和
        // 返回边缘的业务确认意图要么一起提交，要么一起回滚。
        DeliveryCompletionBusinessResult business =
                businessWriter.write(
                        factsRefFactory.issue(persistenceFacts));

        mergeStartCommandCompletion(command, receivedAt,
                "TERMINAL_WEIGHT_FAILURE".equals(fact.completionReason()));
        taskProofPort.completeFromTrustedProof(
                TASK_TYPE,
                TARGET_TYPE,
                fact.sessionUid().toString());
        // 必须在建单成功之后才结束 session 并释放整机占位，否则失败重试期间可能允许
        // 新投递/清运进入，导致原结果无法再与冻结的袋和端口事实对应。
        requireSingle(jdbc.update("""
                        UPDATE dev_delivery_session
                        SET status = 'BUSINESS_CONFIRMED',
                            first_edge_accepted_at =
                                COALESCE(first_edge_accepted_at, ?),
                            first_physical_progress_at =
                                COALESCE(first_physical_progress_at, ?),
                            device_completed_at = ?,
                            ended_at = ?,
                            end_reason = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND status IN (
                              'AUTHORIZATION_QUEUED',
                              'IN_PROGRESS',
                              'RESULT_PENDING_RECOVERY'
                          )
                        """,
                receivedAt,
                receivedAt,
                receivedAt,
                receivedAt,
                fact.completionReason(),
                receivedAt,
                session.id(),
                tenantId,
                organizationId),
                "complete delivery session");
        requireSingle(jdbc.update("""
                        DELETE FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND occupancy_kind = 'DELIVERY'
                          AND delivery_session_id = ?
                        """,
                asset.id(),
                tenantId,
                organizationId,
                assetId,
                session.id()),
                "release delivery occupancy");
        requireSingle(jdbc.update("""
                        UPDATE dev_device_runtime_state
                        SET last_device_event_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        """,
                receivedAt,
                receivedAt,
                assetId,
                tenantId,
                organizationId),
                "touch delivery runtime");
        // 业务确认也是同事务可靠任务。香橙派落盘确认并回传回执后，边缘才可清理原事件。
        confirmationService.registerApplied(
                tenantId,
                organizationId,
                assetId,
                fact.eventUid().toString(),
                fact.payloadSha256(),
                "CREATED",
                business.resultReferences(),
                receivedAt);
        return TrustedDeviceEventApplyResult.APPLIED;
    }

    private TrustedDeviceEventApplyResult quarantine(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long assetId,
            String quarantineReasonCode,
            String confirmationErrorCode,
            String diagnostic) {
        UUID quarantineUid = quarantinePort.quarantine(
                inboxRefFactory.issue(
                        inboxId, tenantId, organizationId),
                quarantineReasonCode,
                diagnostic);
        confirmationService.registerQuarantined(
                tenantId,
                organizationId,
                assetId,
                fact.eventUid().toString(),
                fact.payloadSha256(),
                confirmationErrorCode,
                quarantineUid,
                databaseNow());
        return TrustedDeviceEventApplyResult.QUARANTINED;
    }

    private TrustedDeviceEventApplyResult requirePreviouslyApplied(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long assetId,
            SessionRow session) {
        lockPortRuntimeAndRequirePort(
                session.portId(),
                assetId,
                tenantId,
                organizationId);
        CommandRow command = lockCommand(
                fact,
                session.id(),
                assetId,
                tenantId,
                organizationId);
        PortConfiguration port = loadPortConfiguration(
                session,
                assetId,
                tenantId,
                organizationId);
        verifyFrozenFacts(fact, session, command, port);
        List<ExistingEdge> collisions = findEdgeCollisions(
                fact,
                assetId,
                inboxId);
        if (collisions.size() == 1
                && collisions.getFirst().matches(
                fact,
                assetId,
                inboxId)) {
            return TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED;
        }
        throw new UntrustedInboxSourceException(
                "completed delivery event identity conflicts");
    }

    private List<ExistingEdge> findEdgeCollisions(
            DeliveryCompletePhysicalFact fact,
            long assetId,
            long inboxId) {
        return jdbc.query(
                FIND_EDGE_COLLISIONS_SQL,
                (rs, ignored) -> new ExistingEdge(
                        rs.getLong("id"),
                        rs.getString("event_uid"),
                        rs.getLong("asset_id"),
                        rs.getLong("edge_event_sequence"),
                        rs.getString("canonical_sha256"),
                        rs.getLong("source_inbox_id")),
                fact.eventUid().toString(),
                assetId,
                fact.edgeEventSequence(),
                inboxId);
    }

    private AssetRow lockAsset(
            DeliveryCompletePhysicalFact fact) {
        List<AssetRow> rows = jdbc.query("""
                        SELECT asset.id, asset.tenant_id,
                               asset.organization_id
                        FROM dev_device_asset asset
                        WHERE asset.hardware_sn = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new AssetRow(
                        rs.getLong("id"),
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id")),
                fact.hardwareSn());
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private void lockRuntime(
            long assetId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT asset_id
                        FROM dev_device_runtime_state
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getLong("asset_id"),
                assetId,
                tenantId,
                organizationId);
        if (rows.size() != 1) {
            throw untrusted();
        }
    }

    private SessionRow lockSession(
            DeliveryCompletePhysicalFact fact,
            long assetId,
            long tenantId,
            long organizationId) {
        List<SessionRow> rows = jdbc.query("""
                        SELECT id, port_id, organization_user_id,
                               device_config_version_id,
                               device_config_version_no,
                               device_config_content_sha256,
                               device_config_mcu_payload_sha256,
                               port_config_snapshot_id,
                               delivery_config_version_id,
                               delivery_config_content_sha256,
                               bag_id, bag_uid_snapshot,
                               bag_code_snapshot, status,
                               unit_price_yuan_per_kg,
                               open_balance_floor_cent,
                               max_review_abs_weight_g,
                               negative_weight_anomaly_threshold_g
                        FROM dev_delivery_session
                        WHERE session_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> session(rs),
                fact.sessionUid().toString(),
                tenantId,
                organizationId,
                assetId);
        if (rows.size() != 1
                || !Set.of(
                        "AUTHORIZATION_QUEUED",
                        "IN_PROGRESS",
                        "RESULT_PENDING_RECOVERY",
                        "BUSINESS_CONFIRMED")
                .contains(rows.getFirst().status())) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private void lockOccupancy(
            long sessionId,
            long assetId,
            long tenantId,
            long organizationId) {
        List<Long> rows = jdbc.query("""
                        SELECT delivery_session_id
                        FROM dev_device_occupancy
                        WHERE asset_id = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND occupancy_kind = 'DELIVERY'
                        FOR UPDATE
                        """,
                (rs, ignored) ->
                        rs.getLong("delivery_session_id"),
                assetId,
                tenantId,
                organizationId);
        if (rows.size() != 1 || rows.getFirst() != sessionId) {
            throw untrusted();
        }
    }

    private CommandRow lockCommand(
            DeliveryCompletePhysicalFact fact,
            long sessionId,
            long assetId,
            long tenantId,
            long organizationId) {
        List<CommandRow> rows = jdbc.query("""
                        SELECT id, command_uid, physical_state
                        FROM dev_device_command
                        WHERE command_uid = ?
                          AND tenant_id = ?
                          AND organization_id = ?
                          AND asset_id = ?
                          AND command_type =
                              'START_DELIVERY_SESSION'
                          AND delivery_session_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CommandRow(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("command_uid")),
                        rs.getString("physical_state")),
                fact.commandUid().toString(),
                tenantId,
                organizationId,
                assetId,
                sessionId);
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private void mergeStartCommandCompletion(
            CommandRow command,
            LocalDateTime completedAt,
            boolean terminalWeightFailure) {
        String outcome = terminalWeightFailure ? "PHYSICAL_FAILED" : "PHYSICAL_SUCCEEDED";
        if (outcome.equals(command.physicalState())) {
            return;
        }
        if (!Set.of(
                "QUEUED",
                "EDGE_ACCEPTED",
                "PHYSICAL_STARTED")
                .contains(command.physicalState())) {
            throw new UntrustedInboxSourceException(
                    "delivery completion conflicts with command state");
        }
        requireSingle(jdbc.update("""
                        UPDATE dev_device_command
                        SET physical_state = ?,
                            edge_accepted_at =
                                COALESCE(edge_accepted_at, ?),
                            physical_started_at =
                                COALESCE(physical_started_at, ?),
                            physical_ended_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE id = ?
                          AND physical_state = ?
                        """,
                outcome,
                completedAt,
                completedAt,
                completedAt,
                completedAt,
                command.id(),
                command.physicalState()),
                "merge delivery command completion");
    }

    private void lockPortRuntimeAndRequirePort(
            long portId,
            long assetId,
            long tenantId,
            long organizationId) {
        List<Long> runtime = jdbc.query(
                LOCK_PORT_RUNTIME_SQL,
                (rs, ignored) -> rs.getLong("port_id"),
                portId,
                tenantId,
                organizationId,
                assetId);
        List<Long> ports = jdbc.query(
                LOAD_PORT_SQL,
                (rs, ignored) -> rs.getLong("id"),
                portId,
                tenantId,
                organizationId,
                assetId);
        if (ports.size() != 1 || runtime.size() != 1) {
            throw untrusted();
        }
    }

    private PortConfiguration loadPortConfiguration(
            SessionRow session,
            long assetId,
            long tenantId,
            long organizationId) {
        List<PortConfiguration> rows = jdbc.query(
                LOAD_PORT_CONFIGURATION_SQL,
                (rs, ignored) -> new PortConfiguration(
                        rs.getInt("port_no"),
                        rs.getString("fullness_mode"),
                        rs.getLong("configured_full_weight_g"),
                        rs.getLong("fullness_settle_wait_ms"),
                        rs.getLong("fullness_confirmation_wait_ms"),
                        rs.getLong("weight_measurement_timeout_ms"),
                        rs.getInt("weight_required_sample_count"),
                        rs.getLong("weight_minimum_g"),
                        rs.getLong("weight_maximum_g"),
                        rs.getLong("calibration_version")),
                session.portConfigSnapshotId(),
                session.deviceConfigVersionId(),
                session.portId(),
                tenantId,
                organizationId,
                assetId);
        if (rows.size() != 1) {
            throw untrusted();
        }
        return rows.getFirst();
    }

    private static void verifyFrozenFacts(
            DeliveryCompletePhysicalFact fact,
            SessionRow session,
            CommandRow command,
            PortConfiguration port) {
        long expectedPrice = session.unitPrice()
                .movePointRight(4)
                .longValueExact();
        if (!fact.commandUid().equals(command.uid())
                || fact.portNo() != port.portNo()
                || fact.configurationVersion()
                != session.deviceConfigVersionNo()
                || !fact.configurationContentSha256().equals(
                        HexFormat.of().formatHex(
                                session.deviceConfigContentSha256()))
                || !fact.configurationMcuPayloadSha256().equals(
                        HexFormat.of().formatHex(
                                session.deviceConfigMcuPayloadSha256()))
                || fact.unitPriceTenThousandths() != expectedPrice) {
            throw untrusted();
        }
        requireMeasurementMatchesFrozenPort(
                fact.firstPreOpenMeasurement(),
                port);
        if ("TERMINAL_WEIGHT_FAILURE".equals(fact.completionReason())) {
            if (!isTerminalWeightTimeout(fact.finalPostCloseMeasurement())
                    || fact.finalPostCloseMeasurement().calibrationVersion() != port.calibrationVersion()
                    || port.fullnessMeasurementTimeoutMs() != 5_000
                    || port.weightRequiredSampleCount() != 5) {
                throw untrusted();
            }
        } else {
            requireMeasurementMatchesFrozenPort(fact.finalPostCloseMeasurement(), port);
        }
    }

    private static void requireMeasurementMatchesFrozenPort(
            DeliveryCompleteMeasurement measurement,
            PortConfiguration port) {
        if (!measurementMatchesFrozenPort(
                measurement,
                port.calibrationVersion(),
                port.weightMinimumGrams(),
                port.weightMaximumGrams())) {
            throw untrusted();
        }
    }

    static boolean measurementMatchesFrozenPort(
            DeliveryCompleteMeasurement measurement,
            long calibrationVersion,
            long minimumWeightGrams,
            long maximumWeightGrams) {
        return isNormalWeight(measurement)
                && measurement.calibrationVersion() == calibrationVersion
                && measurement.reportedWeightGrams() >= minimumWeightGrams
                && measurement.reportedWeightGrams() <= maximumWeightGrams;
    }

    private long insertEdgeEvent(
            DeliveryCompletePhysicalFact fact,
            long inboxId,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime receivedAt) {
        requireSingle(jdbc.update("""
                        INSERT INTO dev_edge_event (
                            event_uid, tenant_id, organization_id,
                            asset_id, edge_event_sequence,
                            event_type, delivery_class, schema_version,
                            target_type, target_stable_key_sha256,
                            device_occurred_at, clock_quality,
                            backend_received_at, payload_sha256,
                            canonical_sha256, source_inbox_id, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            'DELIVERY_COMPLETE', 'RELIABLE_FACT', 1,
                            'DELIVERY_SESSION', ?,
                            ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                fact.eventUid().toString(),
                tenantId,
                organizationId,
                assetId,
                fact.edgeEventSequence(),
                sha256(fact.sessionUid().toString()),
                instant(fact.deviceOccurredAt()),
                fact.clockQuality(),
                receivedAt,
                digest(fact.payloadSha256()),
                digest(fact.canonicalSha256()),
                inboxId,
                receivedAt),
                "insert delivery edge event");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_edge_event
                        WHERE event_uid = ?
                        """,
                Long.class,
                fact.eventUid().toString());
        if (id == null) {
            throw new IllegalStateException(
                    "delivery edge event id is missing");
        }
        return id;
    }

    private long insertPhysicalResult(
            DeliveryCompletePhysicalFact fact,
            SessionRow session,
            CommandRow command,
            long edgeEventId,
            long tenantId,
            long organizationId,
            long assetId,
            LocalDateTime receivedAt) {
        DeliveryCompleteMeasurement before =
                fact.firstPreOpenMeasurement();
        DeliveryCompleteMeasurement after =
                fact.finalPostCloseMeasurement();
        DeliveryCompleteDoorCommand door =
                fact.finalDoorCommand();
        requireSingle(jdbc.update("""
                        INSERT INTO dev_physical_result (
                            tenant_id, organization_id, asset_id,
                            port_id, edge_event_id, edge_event_type,
                            command_id, command_type,
                            reported_config_version_no,
                            reported_config_content_sha256,
                            reported_config_mcu_payload_sha256,
                            result_type, delivery_session_id,
                            delivery_pre_measurement_uid,
                            delivery_pre_measurement_status,
                            delivery_pre_weight_g,
                            delivery_pre_last_observed_weight_g,
                            delivery_pre_weight_value_available,
                            delivery_pre_weight_value_kind,
                            delivery_pre_measurement_elapsed_ms,
                            delivery_pre_sample_count,
                            delivery_pre_calibration_version,
                            delivery_pre_sensor_health,
                            delivery_pre_fault_code,
                            delivery_pre_mcu_boot_id,
                            delivery_pre_mcu_event_sequence,
                            delivery_post_measurement_uid,
                            delivery_post_measurement_status,
                            delivery_post_weight_g,
                            delivery_post_last_observed_weight_g,
                            delivery_post_weight_value_available,
                            delivery_post_weight_value_kind,
                            delivery_post_measurement_elapsed_ms,
                            delivery_post_sample_count,
                            delivery_post_calibration_version,
                            delivery_post_sensor_health,
                            delivery_post_fault_code,
                            delivery_post_mcu_boot_id,
                            delivery_post_mcu_event_sequence,
                            delivery_net_weight_g,
                            delivery_final_door_command,
                            delivery_final_door_output_status,
                            delivery_final_door_physical_state_basis,
                            delivery_completion_reason,
                            delivery_manual_review_required,
                            negative_weight_anomaly,
                            created_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            ?, 'DELIVERY_COMPLETE',
                            ?, 'START_DELIVERY_SESSION',
                            ?, ?, ?,
                            'DELIVERY', ?,
                            ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                tenantId,
                organizationId,
                assetId,
                session.portId(),
                edgeEventId,
                command.id(),
                session.deviceConfigVersionNo(),
                session.deviceConfigContentSha256(),
                session.deviceConfigMcuPayloadSha256(),
                session.id(),
                before.measurementUid().toString(),
                before.status(),
                before.reportedWeightGrams(),
                before.weightValueAvailable(),
                before.weightValueKind(),
                before.measurementElapsedMs(),
                before.sampleCount(),
                before.calibrationVersion(),
                before.sensorHealth(),
                before.faultCode(),
                before.mcuBootId(),
                before.mcuEventSequence(),
                after.measurementUid().toString(),
                after.status(),
                after.reportedWeightGrams(),
                after.weightValueAvailable(),
                after.weightValueKind(),
                after.measurementElapsedMs(),
                after.sampleCount(),
                after.calibrationVersion(),
                after.sensorHealth(),
                after.faultCode(),
                after.mcuBootId(),
                after.mcuEventSequence(),
                fact.deliveryNetWeightGrams(),
                door.command(),
                door.outputStatus(),
                door.physicalStateBasis(),
                fact.completionReason(),
                fact.manualReviewRequired(),
                fact.negativeWeightAnomaly(),
                receivedAt),
                "insert delivery physical result");
        Long id = jdbc.queryForObject("""
                        SELECT id
                        FROM dev_physical_result
                        WHERE delivery_session_id = ?
                        """,
                Long.class,
                session.id());
        if (id == null) {
            throw new IllegalStateException(
                    "delivery physical result id is missing");
        }
        return id;
    }

    DeliveryCompletePhysicalFact parse(String normalizedPayload) {
        JsonNode root = objectMapper.readTree(normalizedPayload);
        JsonNode source = requiredObject(root, "trustedSource");
        JsonNode event = requiredObject(root, "event");
        JsonNode target = requiredObject(event, "target");
        JsonNode payload = requiredObject(event, "payload");
        if (!MESSAGE_KIND.equals(requiredText(event, "eventType"))
                || !"RELIABLE_FACT".equals(
                requiredText(event, "deliveryClass"))
                || !"DELIVERY_SESSION".equals(
                requiredText(target, "type"))) {
            throw new IllegalArgumentException(
                    "delivery completion envelope constants differ");
        }
        UUID sessionUid = uuid(payload, "sessionUid");
        if (!sessionUid.toString().equals(
                requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "delivery completion target differs from session");
        }
        String sourceHardwareSn = requiredText(source, "deviceName");
        JsonNode frozen = requiredObject(payload, "frozenConfig");
        List<DeliveryCompletePhoto> photos = new ArrayList<>();
        JsonNode photoArray = requiredArray(payload, "photos");
        photoArray.forEach(node -> photos.add(photo(node)));
        return new DeliveryCompletePhysicalFact(
                uuid(event, "eventUid"),
                uuid(event, "commandUid"),
                sessionUid,
                sourceHardwareSn,
                positiveLong(event, "edgeEventSequence"),
                nullableInstant(event, "occurredAt"),
                requiredText(event, "clockQuality"),
                requiredText(event, "payloadSha256"),
                requiredText(root, "eventCanonicalSha256"),
                Math.toIntExact(positiveLong(payload, "portNo")),
                measurement(payload.get("firstPreOpenMeasurement")),
                measurement(payload.get("finalPostCloseMeasurement")),
                nullableLong(payload, "deliveryNetWeightGrams"),
                door(payload.get("finalDoorCommand")),
                requiredText(payload, "completionReason"),
                requiredBoolean(payload, "manualReviewRequired"),
                requiredBoolean(payload, "negativeWeightAnomaly"),
                positiveLong(frozen, "version"),
                requiredText(frozen, "contentSha256"),
                requiredText(frozen, "mcuPayloadSha256"),
                positiveLong(payload, "unitPriceTenThousandths"),
                photos);
    }

    private static DeliveryCompleteMeasurement measurement(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return new DeliveryCompleteMeasurement(
                uuid(node, "measurementUid"),
                requiredText(node, "status"),
                requiredBoolean(node, "weightValueAvailable"),
                nullableLong(node, "reportedWeightGrams"),
                requiredText(node, "weightValueKind"),
                nonNegativeLong(node, "measurementElapsedMs"),
                Math.toIntExact(nonNegativeLong(node, "sampleCount")),
                nonNegativeLong(node, "calibrationVersion"),
                requiredText(node, "sensorHealth"),
                nullableText(node, "faultCode"),
                positiveLong(node, "mcuBootId"),
                positiveLong(node, "mcuEventSequence"));
    }

    private static DeliveryCompleteDoorCommand door(JsonNode node) {
        if (node == null || node.isNull()) {
            return null;
        }
        return new DeliveryCompleteDoorCommand(
                requiredText(node, "command"),
                requiredText(node, "outputStatus"),
                requiredText(node, "physicalStateBasis"));
    }

    private static DeliveryCompletePhoto photo(JsonNode node) {
        return new DeliveryCompletePhoto(
                requiredText(node, "slot"),
                requiredText(node, "status"),
                nullableUuid(node, "photoUid"),
                nullableText(node, "url"),
                nullableText(node, "sha256"),
                nullableLong(node, "sizeBytes"),
                nullableInstant(node, "capturedAt"),
                nullableText(node, "missingReason"));
    }

    private static void requireSupportedCompletion(
            DeliveryCompletePhysicalFact fact) {
        DeliveryCompleteMeasurement before =
                fact.firstPreOpenMeasurement();
        DeliveryCompleteMeasurement after =
                fact.finalPostCloseMeasurement();
        DeliveryCompleteDoorCommand door =
                fact.finalDoorCommand();
        boolean timeout = "TERMINAL_WEIGHT_FAILURE".equals(fact.completionReason());
        boolean supportedResult = timeout
                ? isTerminalWeightTimeout(after) && fact.deliveryNetWeightGrams() == null
                : isNormalWeight(after) && fact.deliveryNetWeightGrams() != null
                && Set.of("USER_ENDED", "SELECTION_WINDOW_EXPIRED").contains(fact.completionReason());
        if (!isNormalWeight(before)
                || !supportedResult
                || before.measurementUid().equals(
                        after.measurementUid())
                || (before.mcuBootId() == after.mcuBootId()
                && before.mcuEventSequence()
                == after.mcuEventSequence())
                || (timeout && (before.mcuBootId() != after.mcuBootId()
                || after.mcuEventSequence() <= before.mcuEventSequence()))
                || door == null
                || !"CLOSE".equals(door.command())
                || !Set.of(
                        "COMMAND_DISPATCHED",
                        "COALESCED_WITH_EXISTING_CLOSE")
                .contains(door.outputStatus())
                || !"NOT_OBSERVABLE".equals(
                        door.physicalStateBasis())
                || fact.manualReviewRequired()
                || fact.photos().size() != 4
                || !fact.photos().stream()
                .map(DeliveryCompletePhoto::slot)
                .collect(java.util.stream.Collectors.toSet())
                .equals(Set.of(
                        "BEFORE_INNER",
                        "BEFORE_OUTER",
                        "AFTER_INNER",
                        "AFTER_OUTER"))) {
            throw new IllegalArgumentException(
                    "delivery requires usable weights or an exact terminal weight timeout");
        }
    }

    private static boolean isTerminalWeightTimeout(DeliveryCompleteMeasurement measurement) {
        // This is a failed measurement, not a zero-weight sample, reboot or generic cancellation.
        return measurement != null && measurement.measurementUid() != null
                && "TIMEOUT".equals(measurement.status())
                && !measurement.weightValueAvailable() && measurement.reportedWeightGrams() == null
                && "NONE".equals(measurement.weightValueKind())
                && "TIMEOUT".equals(measurement.sensorHealth())
                && "WEIGHT_TIMEOUT".equals(measurement.faultCode())
                && measurement.measurementElapsedMs() == 5_000
                && measurement.sampleCount() >= 0 && measurement.sampleCount() < 5
                && measurement.calibrationVersion() >= 0 && measurement.calibrationVersion() <= 4_294_967_295L
                && measurement.mcuBootId() >= 1 && measurement.mcuBootId() <= 9_007_199_254_740_991L
                && measurement.mcuEventSequence() >= 1 && measurement.mcuEventSequence() <= 4_294_967_295L;
    }

    private static boolean isNormalWeight(DeliveryCompleteMeasurement measurement) {
        if (measurement == null || measurement.measurementUid() == null
                || !measurement.weightValueAvailable() || measurement.reportedWeightGrams() == null
                || !"OK".equals(measurement.sensorHealth()) || measurement.faultCode() != null
                || measurement.reportedWeightGrams() < Integer.MIN_VALUE
                || measurement.reportedWeightGrams() > Integer.MAX_VALUE
                || measurement.measurementElapsedMs() < 0 || measurement.measurementElapsedMs() > 4_294_967_295L
                || measurement.calibrationVersion() < 0 || measurement.calibrationVersion() > 4_294_967_295L
                || measurement.mcuBootId() < 1 || measurement.mcuBootId() > 9_007_199_254_740_991L
                || measurement.mcuEventSequence() < 1 || measurement.mcuEventSequence() > 4_294_967_295L) {
            return false;
        }
        // Preserve the frozen fixed-frame single-sample mean; do not call it native five-point proof.
        return ("STABLE".equals(measurement.status())
                && "STABLE_WINDOW_MEAN".equals(measurement.weightValueKind())
                && measurement.sampleCount() >= 1 && measurement.sampleCount() <= 65_535)
                || ("UNSTABLE".equals(measurement.status())
                && "TIMEOUT_MEDIAN".equals(measurement.weightValueKind())
                && measurement.measurementElapsedMs() == 5_000
                && measurement.sampleCount() >= 5 && measurement.sampleCount() <= 32);
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)",
                LocalDateTime.class);
    }

    private static SessionRow session(ResultSet rs)
            throws SQLException {
        return new SessionRow(
                rs.getLong("id"),
                rs.getLong("port_id"),
                rs.getLong("organization_user_id"),
                rs.getLong("device_config_version_id"),
                rs.getLong("device_config_version_no"),
                rs.getBytes("device_config_content_sha256"),
                rs.getBytes("device_config_mcu_payload_sha256"),
                rs.getLong("port_config_snapshot_id"),
                rs.getLong("delivery_config_version_id"),
                rs.getBytes("delivery_config_content_sha256"),
                rs.getLong("bag_id"),
                UUID.fromString(rs.getString("bag_uid_snapshot")),
                rs.getString("bag_code_snapshot"),
                rs.getString("status"),
                rs.getBigDecimal("unit_price_yuan_per_kg"),
                rs.getLong("open_balance_floor_cent"),
                rs.getLong("max_review_abs_weight_g"),
                rs.getLong(
                        "negative_weight_anomaly_threshold_g"));
    }

    private static JsonNode requiredObject(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw new IllegalArgumentException(
                    field + " must be an object");
        }
        return value;
    }

    private static JsonNode requiredArray(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isArray()) {
            throw new IllegalArgumentException(
                    field + " must be an array");
        }
        return value;
    }

    private static String requiredText(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()) {
            throw new IllegalArgumentException(
                    field + " must be text");
        }
        return value.asText();
    }

    private static String nullableText(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw new IllegalArgumentException(
                    field + " must be nullable text");
        }
        return value.asText();
    }

    private static boolean requiredBoolean(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw new IllegalArgumentException(
                    field + " must be a boolean");
        }
        return value.booleanValue();
    }

    private static UUID uuid(JsonNode parent, String field) {
        return UUID.fromString(requiredText(parent, field));
    }

    private static UUID nullableUuid(JsonNode parent, String field) {
        String value = nullableText(parent, field);
        return value == null ? null : UUID.fromString(value);
    }

    private static long positiveLong(
            JsonNode parent,
            String field) {
        long value = exactLong(parent, field);
        if (value <= 0) {
            throw new IllegalArgumentException(
                    field + " must be positive");
        }
        return value;
    }

    private static long nonNegativeLong(
            JsonNode parent,
            String field) {
        long value = exactLong(parent, field);
        if (value < 0) {
            throw new IllegalArgumentException(
                    field + " must be non-negative");
        }
        return value;
    }

    private static Long nullableLong(
            JsonNode parent,
            String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return value.decimalValue().longValueExact();
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    field + " must be an integer",
                    exception);
        }
    }

    private static long exactLong(
            JsonNode parent,
            String field) {
        Long value = nullableLong(parent, field);
        if (value == null) {
            throw new IllegalArgumentException(
                    field + " must be an integer");
        }
        return value;
    }

    private static Instant nullableInstant(
            JsonNode parent,
            String field) {
        String value = nullableText(parent, field);
        return value == null ? null : Instant.parse(value);
    }

    private static LocalDateTime instant(Instant value) {
        return value == null
                ? null
                : LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private static byte[] digest(String value) {
        return HexFormat.of().parseHex(value);
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        }
    }

    private static void requireSingle(
            int updated,
            String operation) {
        if (updated != 1) {
            throw new IllegalStateException(
                    operation + " affected " + updated + " rows");
        }
    }

    private static UntrustedInboxSourceException untrusted() {
        return new UntrustedInboxSourceException(
                "delivery completion target is not authoritative");
    }

    private record AssetRow(
            long id,
            long tenantId,
            long organizationId) {
    }

    private record CommandRow(
            long id,
            UUID uid,
            String physicalState) {
    }

    private record PortConfiguration(
            int portNo,
            String fullnessMode,
            long configuredFullWeightGrams,
            long fullnessSettleWaitMs,
            long fullnessConfirmationWaitMs,
            long fullnessMeasurementTimeoutMs,
            int weightRequiredSampleCount,
            long weightMinimumGrams,
            long weightMaximumGrams,
            long calibrationVersion) {
    }

    private record SessionRow(
            long id,
            long portId,
            long organizationUserId,
            long deviceConfigVersionId,
            long deviceConfigVersionNo,
            byte[] deviceConfigContentSha256,
            byte[] deviceConfigMcuPayloadSha256,
            long portConfigSnapshotId,
            long deliveryConfigVersionId,
            byte[] deliveryConfigContentSha256,
            long bagId,
            UUID bagUid,
            String bagCode,
            String status,
            BigDecimal unitPrice,
            long openBalanceFloorCent,
            long maxReviewAbsWeightGrams,
            long negativeWeightThresholdGrams) {
    }

    private record ExistingEdge(
            long id,
            String eventUid,
            long assetId,
            long sequence,
            String canonicalSha256,
            long inboxId) {

        private boolean matches(
                DeliveryCompletePhysicalFact fact,
                long expectedAssetId,
                long expectedInboxId) {
            return eventUid.equals(fact.eventUid().toString())
                    && assetId == expectedAssetId
                    && sequence == fact.edgeEventSequence()
                    && canonicalSha256.equals(
                    fact.canonicalSha256())
                    && inboxId == expectedInboxId;
        }
    }
}
