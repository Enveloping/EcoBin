package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.AcceptanceEvidenceSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactoryAcceptanceProgressView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactoryBagProgressView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactoryProgressView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactorySealProgressView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ReliableTaskAttemptSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ReliableTaskProgressView;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.DeviceScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.DeviceScopeAuthorizationQuery;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 平台侧厂家接入与封存进度的一致只读快照。
 *
 * <p>权威验收证据只能按资产保存的摘要关联；最新证据仅用于诊断，不能替代
 * 当前 PASSED 结论绑定的证据。可靠任务 DONE 也不等于设备已经完成封存。</p>
 */
@Service
public class FactoryProgressQueryService {

    private static final String ACCEPTANCE_TASK =
            "REQUEST_DEVICE_ACCEPTANCE";

    static final String VERIFIED_FACTORY_BAG_COUNT_SQL = """
            SELECT COUNT(*)
            FROM dev_factory_installed_bag bag
            WHERE bag.asset_id = ?
              AND (
                  bag.installation_source = 'LEGACY_GRANDFATHERED'
                  OR (
                      bag.installation_source = 'FACTORY_MINIAPP'
                      AND bag.installed_by_factory_operator_id IS NOT NULL
                      AND bag.label_item_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM rec_bag_label_claim claim
                          WHERE claim.label_item_id = bag.label_item_id
                            AND claim.asset_id = bag.asset_id
                            AND claim.port_no = bag.port_no
                            AND claim.released_at IS NULL
                      )
                  )
              )
            """;

    static final String CURRENT_ACCEPTANCE_TASK_SQL = """
            SELECT task.task_uid,
                   task.state,
                   task.blocked_reason_code,
                   task.blocked_diagnostic,
                   attempt.attempt_no,
                   attempt.technical_result,
                   attempt.http_status,
                   attempt.external_api_error_code,
                   attempt.external_request_id,
                   attempt.redacted_diagnostic,
                   attempt.result_recorded_at
            FROM ops_reliable_task task
            LEFT JOIN ops_task_attempt attempt
              ON attempt.id = (
                  SELECT MAX(latest_attempt.id)
                  FROM ops_task_attempt latest_attempt
                  WHERE latest_attempt.task_id = task.id
              )
            WHERE task.scope_kind = 'PLATFORM'
              AND task.tenant_id IS NULL
              AND task.organization_id IS NULL
              AND task.source_device_asset_id = ?
              AND task.task_type = ?
              AND CAST(JSON_UNQUOTE(JSON_EXTRACT(
                    task.redacted_execution_snapshot,
                    '$.payload.factoryBagRevision'
                  )) AS UNSIGNED) = ?
              AND JSON_UNQUOTE(JSON_EXTRACT(
                    task.redacted_execution_snapshot,
                    '$.payload.factoryBagSetSha256'
                  )) = ?
            ORDER BY task.id DESC
            LIMIT 1
            """;

    private final JdbcTemplate jdbc;
    private final DeviceScopeAuthorizationPort authorizationPort;
    private final ObjectMapper objectMapper;

    public FactoryProgressQueryService(
            JdbcTemplate jdbc,
            DeviceScopeAuthorizationPort authorizationPort,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.authorizationPort = authorizationPort;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public FactoryProgressView query(String hardwareSn) {
        authorizationPort.authorize(new DeviceScopeAuthorizationQuery(
                true, null, null, "device.read"));
        AssetProgressRow asset = asset(normalizeHardwareSn(hardwareSn));
        LocalDateTime fetchedAt = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (fetchedAt == null) {
            throw new IllegalStateException("database time is unavailable");
        }

        Integer bagCount = jdbc.queryForObject(
                VERIFIED_FACTORY_BAG_COUNT_SQL,
                Integer.class,
                asset.id());
        int verifiedCount = bagCount == null ? 0 : bagCount;
        AcceptanceEvidenceSummary authoritative =
                asset.acceptanceEvidenceSha256() == null
                        ? null
                        : evidenceBySha(
                                asset.id(),
                                asset.acceptanceEvidenceSha256());
        AcceptanceEvidenceSummary latest = latestEvidence(asset.id());
        TaskProgressRow acceptanceRequest = currentAcceptanceTask(
                asset.id(),
                ACCEPTANCE_TASK,
                asset.factoryBagRevision(),
                asset.factoryBagSetSha256());
        SealProgressRow seal = currentSeal(
                asset.id(), asset.acceptanceGeneration());
        return project(new ProgressSource(
                        asset,
                        verifiedCount,
                        authoritative,
                        latest,
                        acceptanceRequest,
                        seal),
                fetchedAt.toInstant(ZoneOffset.UTC));
    }

    static FactoryProgressView project(
            ProgressSource source,
            Instant fetchedAt) {
        AssetProgressRow asset = source.asset();
        FactoryBagProgressView bags = new FactoryBagProgressView(
                asset.expectedPortCount(),
                source.verifiedBagCount(),
                source.verifiedBagCount() == asset.expectedPortCount(),
                asset.factoryBagRevision());
        FactoryAcceptanceProgressView acceptance =
                new FactoryAcceptanceProgressView(
                        asset.acceptanceStatus(),
                        asset.acceptanceGeneration(),
                        asset.currentFailureReasons(),
                        instant(asset.lastAcceptanceEvaluatedAt()),
                        instant(asset.acceptedAt()),
                        source.authoritativeEvidence(),
                        source.latestEvidence());
        ReliableTaskProgressView acceptanceRequest =
                taskView(source.acceptanceRequest());
        FactorySealProgressView seal = sealView(
                source.seal(), asset.acceptanceGeneration());
        ProjectionState state = projectionState(
                asset, bags, acceptance, acceptanceRequest, seal);
        return new FactoryProgressView(
                bags,
                acceptance,
                acceptanceRequest,
                seal,
                state.currentStage(),
                state.status(),
                state.blockingCode(),
                state.nextActionCodes(),
                fetchedAt);
    }

    private static ProjectionState projectionState(
            AssetProgressRow asset,
            FactoryBagProgressView bags,
            FactoryAcceptanceProgressView acceptance,
            ReliableTaskProgressView acceptanceRequest,
            FactorySealProgressView seal) {
        if (!"NORMAL".equals(asset.lifecycleStatus())
                && !"SEALED".equals(seal.status())) {
            return blocked(
                    "DEVICE_ASSET",
                    "DEVICE_ASSET_UNAVAILABLE",
                    "RESTORE_DEVICE_ASSET");
        }
        if (!bags.complete()) {
            return new ProjectionState(
                    "FACTORY_BAGS",
                    "WAITING_OPERATOR",
                    "FACTORY_BAGS_INCOMPLETE",
                    List.of("SCAN_FACTORY_BAGS"));
        }
        if ("FAILED".equals(acceptance.status())) {
            String failure = acceptance.currentFailureReasons().isEmpty()
                    ? "MACHINE_ACCEPTANCE_FAILED"
                    : acceptance.currentFailureReasons().getFirst();
            return new ProjectionState(
                    "MACHINE_ACCEPTANCE",
                    "BLOCKED",
                    failure,
                    List.of(
                            "VIEW_ACCEPTANCE_FAILURES",
                            "REEVALUATE_ACCEPTANCE"));
        }
        if (!"PASSED".equals(acceptance.status())) {
            if ("BLOCKED".equals(acceptanceRequest.taskState())) {
                return blocked(
                        "MACHINE_ACCEPTANCE",
                        fallback(
                                acceptanceRequest.blockedReasonCode(),
                                "ACCEPTANCE_REQUEST_BLOCKED"),
                        "OPEN_RELIABLE_TASK",
                        "RESOLVE_ACCEPTANCE_TASK_BLOCKER");
            }
            if ("CANCELLED".equals(acceptanceRequest.taskState())) {
                return blocked(
                        "MACHINE_ACCEPTANCE",
                        "ACCEPTANCE_REQUEST_CANCELLED",
                        "REEVALUATE_ACCEPTANCE");
            }
            return new ProjectionState(
                    "MACHINE_ACCEPTANCE",
                    "IN_PROGRESS",
                    null,
                    List.of(acceptanceRequest.taskUid() == null
                            ? "WAIT_FOR_ACCEPTANCE_REQUEST"
                            : "WAIT_FOR_ACCEPTANCE_EVIDENCE"));
        }
        if (acceptance.authoritativeEvidence() == null) {
            return blocked(
                    "MACHINE_ACCEPTANCE",
                    "ACCEPTANCE_EVIDENCE_MISSING",
                    "VIEW_ACCEPTANCE_FAILURES",
                    "REEVALUATE_ACCEPTANCE");
        }
        return switch (seal.status()) {
            case "NOT_ISSUED" -> new ProjectionState(
                    "FACTORY_SEAL_AUTHORIZATION",
                    "IN_PROGRESS",
                    null,
                    List.of("WAIT_FOR_FACTORY_SEAL_AUTHORIZATION"));
            case "PENDING" -> pendingSeal(seal);
            case "ACKNOWLEDGED" -> new ProjectionState(
                    "END_FACTORY_MODE",
                    "WAITING_OPERATOR",
                    null,
                    List.of("CONFIRM_END_FACTORY_MODE"));
            case "CANCELLED" -> blocked(
                    "FACTORY_SEAL_AUTHORIZATION",
                    fallback(
                            seal.cancellationReason(),
                            "FACTORY_SEAL_AUTHORIZATION_CANCELLED"),
                    "VIEW_FACTORY_SEAL_CANCELLATION",
                    "REEVALUATE_ACCEPTANCE");
            case "SEALED" -> new ProjectionState(
                    "FACTORY_SEALED",
                    "COMPLETED",
                    null,
                    List.of());
            default -> blocked(
                    "FACTORY_SEAL_AUTHORIZATION",
                    "FACTORY_SEAL_STATUS_UNKNOWN",
                    "CONTACT_SUPPORT");
        };
    }

    private static ProjectionState pendingSeal(
            FactorySealProgressView seal) {
        if ("BLOCKED".equals(seal.taskState())) {
            return blocked(
                    "FACTORY_SEAL_AUTHORIZATION",
                    fallback(
                            seal.blockedReasonCode(),
                            "FACTORY_SEAL_TASK_BLOCKED"),
                    "OPEN_RELIABLE_TASK",
                    "RESOLVE_FACTORY_SEAL_TASK_BLOCKER");
        }
        if ("CANCELLED".equals(seal.taskState())) {
            return blocked(
                    "FACTORY_SEAL_AUTHORIZATION",
                    "FACTORY_SEAL_TASK_CANCELLED",
                    "OPEN_RELIABLE_TASK",
                    "REEVALUATE_ACCEPTANCE");
        }
        return new ProjectionState(
                "FACTORY_SEAL_AUTHORIZATION",
                "IN_PROGRESS",
                null,
                List.of("WAIT_FOR_FACTORY_SEAL_ACKNOWLEDGEMENT"));
    }

    private static ProjectionState blocked(
            String stage,
            String code,
            String... actions) {
        return new ProjectionState(
                stage, "BLOCKED", code, List.of(actions));
    }

    private AssetProgressRow asset(String hardwareSn) {
        List<AssetProgressRow> rows = jdbc.query("""
                        SELECT id,
                               expected_port_count,
                               factory_bag_revision,
                               factory_bag_set_sha256,
                               acceptance_status,
                               acceptance_generation,
                               acceptance_failure_json,
                               acceptance_evidence_sha256,
                               last_acceptance_evaluated_at,
                               accepted_at,
                               lifecycle_status
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        """,
                (rs, ignored) -> new AssetProgressRow(
                        rs.getLong("id"),
                        rs.getInt("expected_port_count"),
                        rs.getLong("factory_bag_revision"),
                        rs.getBytes("factory_bag_set_sha256"),
                        rs.getString("acceptance_status"),
                        rs.getLong("acceptance_generation"),
                        failureReasons(
                                rs.getString("acceptance_failure_json")),
                        rs.getBytes("acceptance_evidence_sha256"),
                        rs.getObject(
                                "last_acceptance_evaluated_at",
                                LocalDateTime.class),
                        rs.getObject("accepted_at", LocalDateTime.class),
                        rs.getString("lifecycle_status")),
                hardwareSn);
        if (rows.size() != 1) {
            throw notFound();
        }
        return rows.getFirst();
    }

    private AcceptanceEvidenceSummary evidenceBySha(
            long assetId,
            byte[] evidenceSha256) {
        List<AcceptanceEvidenceSummary> rows = jdbc.query("""
                        SELECT evidence_uid,
                               evaluation_status,
                               LOWER(HEX(evidence_sha256)) evidence_sha256,
                               received_at
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                          AND evidence_sha256 = ?
                        """,
                FactoryProgressQueryService::evidenceSummary,
                assetId,
                evidenceSha256);
        return rows.size() == 1 ? rows.getFirst() : null;
    }

    private AcceptanceEvidenceSummary latestEvidence(long assetId) {
        List<AcceptanceEvidenceSummary> rows = jdbc.query("""
                        SELECT evidence_uid,
                               evaluation_status,
                               LOWER(HEX(evidence_sha256)) evidence_sha256,
                               received_at
                        FROM dev_device_acceptance_evidence
                        WHERE asset_id = ?
                        ORDER BY received_at DESC, id DESC
                        LIMIT 1
                        """,
                FactoryProgressQueryService::evidenceSummary,
                assetId);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private TaskProgressRow currentAcceptanceTask(
            long assetId,
            String taskType,
            long factoryBagRevision,
            byte[] factoryBagSetSha256) {
        if (factoryBagSetSha256 == null) {
            return null;
        }
        List<TaskProgressRow> rows = jdbc.query(
                CURRENT_ACCEPTANCE_TASK_SQL,
                (rs, ignored) -> taskProgress(rs),
                assetId,
                taskType,
                factoryBagRevision,
                HexFormat.of().formatHex(factoryBagSetSha256));
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private SealProgressRow currentSeal(
            long assetId,
            long acceptanceGeneration) {
        if (acceptanceGeneration == 0) {
            return null;
        }
        List<SealProgressRow> rows = jdbc.query("""
                        SELECT authorization.authorization_status,
                               authorization.acceptance_generation,
                               authorization.cancellation_reason,
                               authorization.acknowledged_at,
                               authorization.sealed_at,
                               authorization.cleanup_completed_at,
                               authorization.completion_received_at,
                               task.task_uid,
                               task.state,
                               task.blocked_reason_code,
                               task.blocked_diagnostic,
                               attempt.attempt_no,
                               attempt.technical_result,
                               attempt.http_status,
                               attempt.external_api_error_code,
                               attempt.external_request_id,
                               attempt.redacted_diagnostic,
                               attempt.result_recorded_at
                        FROM dev_factory_seal_authorization authorization
                        LEFT JOIN ops_reliable_task task
                          ON task.task_uid =
                              authorization.reliable_task_uid
                        LEFT JOIN ops_task_attempt attempt
                          ON attempt.id = (
                              SELECT MAX(latest_attempt.id)
                              FROM ops_task_attempt latest_attempt
                              WHERE latest_attempt.task_id = task.id
                          )
                        WHERE authorization.asset_id = ?
                          AND authorization.acceptance_generation = ?
                        """,
                (rs, ignored) -> new SealProgressRow(
                        rs.getString("authorization_status"),
                        rs.getLong("acceptance_generation"),
                        rs.getString("cancellation_reason"),
                        nullableUuid(rs, "task_uid"),
                        rs.getString("state"),
                        rs.getString("blocked_reason_code"),
                        rs.getString("blocked_diagnostic"),
                        attempt(rs),
                        rs.getObject(
                                "acknowledged_at", LocalDateTime.class),
                        rs.getObject("sealed_at", LocalDateTime.class),
                        rs.getObject(
                                "cleanup_completed_at",
                                LocalDateTime.class),
                        rs.getObject(
                                "completion_received_at",
                                LocalDateTime.class)),
                assetId,
                acceptanceGeneration);
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private List<String> failureReasons(String json) {
        if (json == null) {
            return List.of();
        }
        try {
            JsonNode node = objectMapper.readTree(json);
            if (!node.isArray()) {
                throw new IllegalStateException(
                        "asset acceptance failures are not an array");
            }
            List<String> result = new ArrayList<>();
            node.forEach(value -> result.add(value.asText()));
            return List.copyOf(result);
        } catch (IllegalStateException exception) {
            throw exception;
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "asset acceptance failures cannot be decoded",
                    exception);
        }
    }

    private static AcceptanceEvidenceSummary evidenceSummary(
            ResultSet rs,
            int ignored) throws SQLException {
        return new AcceptanceEvidenceSummary(
                UUID.fromString(rs.getString("evidence_uid")),
                rs.getString("evaluation_status"),
                rs.getString("evidence_sha256"),
                instant(rs.getObject("received_at", LocalDateTime.class)));
    }

    private static TaskProgressRow taskProgress(ResultSet rs)
            throws SQLException {
        return new TaskProgressRow(
                nullableUuid(rs, "task_uid"),
                rs.getString("state"),
                rs.getString("blocked_reason_code"),
                rs.getString("blocked_diagnostic"),
                attempt(rs));
    }

    private static ReliableTaskAttemptSummary attempt(ResultSet rs)
            throws SQLException {
        Long attemptNo = nullableLong(rs, "attempt_no");
        if (attemptNo == null) {
            return null;
        }
        return new ReliableTaskAttemptSummary(
                attemptNo,
                rs.getString("technical_result"),
                nullableInteger(rs, "http_status"),
                rs.getString("external_api_error_code"),
                rs.getString("external_request_id"),
                rs.getString("redacted_diagnostic"),
                instant(rs.getObject(
                        "result_recorded_at", LocalDateTime.class)));
    }

    private static ReliableTaskProgressView taskView(TaskProgressRow row) {
        return row == null
                ? new ReliableTaskProgressView(
                        null, null, null, null, null)
                : new ReliableTaskProgressView(
                        row.taskUid(),
                        row.taskState(),
                        row.blockedReasonCode(),
                        row.blockedDiagnostic(),
                        row.latestAttempt());
    }

    private static FactorySealProgressView sealView(
            SealProgressRow row,
            long currentGeneration) {
        return row == null
                ? new FactorySealProgressView(
                        "NOT_ISSUED",
                        currentGeneration,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null)
                : new FactorySealProgressView(
                        row.status(),
                        row.generation(),
                        row.cancellationReason(),
                        row.taskUid(),
                        row.taskState(),
                        row.blockedReasonCode(),
                        row.blockedDiagnostic(),
                        row.latestAttempt(),
                        instant(row.acknowledgedAt()),
                        instant(row.sealedAt()),
                        instant(row.cleanupCompletedAt()),
                        instant(row.completionReceivedAt()));
    }

    private static UUID nullableUuid(ResultSet rs, String column)
            throws SQLException {
        String value = rs.getString(column);
        return value == null ? null : UUID.fromString(value);
    }

    private static Long nullableLong(ResultSet rs, String column)
            throws SQLException {
        long value = rs.getLong(column);
        return rs.wasNull() ? null : value;
    }

    private static Integer nullableInteger(ResultSet rs, String column)
            throws SQLException {
        int value = rs.getInt(column);
        return rs.wasNull() ? null : value;
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static String fallback(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value;
    }

    private static String normalizeHardwareSn(String value) {
        if (value == null || value.isBlank()) {
            throw invalid("hardwareSn不能为空");
        }
        String result = value.trim();
        if (result.length() > 64
                || !result.matches("[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")) {
            throw invalid("硬件序列号格式无效");
        }
        return result;
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400,
                "COMMON.INVALID_REQUEST",
                message,
                false,
                Map.of());
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "COMMON.RESOURCE_NOT_FOUND",
                "设备不存在或不可用");
    }

    record ProgressSource(
            AssetProgressRow asset,
            int verifiedBagCount,
            AcceptanceEvidenceSummary authoritativeEvidence,
            AcceptanceEvidenceSummary latestEvidence,
            TaskProgressRow acceptanceRequest,
            SealProgressRow seal) {
    }

    record AssetProgressRow(
            long id,
            int expectedPortCount,
            long factoryBagRevision,
            byte[] factoryBagSetSha256,
            String acceptanceStatus,
            long acceptanceGeneration,
            List<String> currentFailureReasons,
            byte[] acceptanceEvidenceSha256,
            LocalDateTime lastAcceptanceEvaluatedAt,
            LocalDateTime acceptedAt,
            String lifecycleStatus) {

        AssetProgressRow {
            currentFailureReasons = List.copyOf(currentFailureReasons);
            factoryBagSetSha256 = factoryBagSetSha256 == null
                    ? null : factoryBagSetSha256.clone();
            acceptanceEvidenceSha256 = acceptanceEvidenceSha256 == null
                    ? null : acceptanceEvidenceSha256.clone();
        }

        @Override
        public byte[] acceptanceEvidenceSha256() {
            return acceptanceEvidenceSha256 == null
                    ? null : acceptanceEvidenceSha256.clone();
        }

        @Override
        public byte[] factoryBagSetSha256() {
            return factoryBagSetSha256 == null
                    ? null : factoryBagSetSha256.clone();
        }
    }

    record TaskProgressRow(
            UUID taskUid,
            String taskState,
            String blockedReasonCode,
            String blockedDiagnostic,
            ReliableTaskAttemptSummary latestAttempt) {
    }

    record SealProgressRow(
            String status,
            long generation,
            String cancellationReason,
            UUID taskUid,
            String taskState,
            String blockedReasonCode,
            String blockedDiagnostic,
            ReliableTaskAttemptSummary latestAttempt,
            LocalDateTime acknowledgedAt,
            LocalDateTime sealedAt,
            LocalDateTime cleanupCompletedAt,
            LocalDateTime completionReceivedAt) {
    }

    private record ProjectionState(
            String currentStage,
            String status,
            String blockingCode,
            List<String> nextActionCodes) {

        private ProjectionState {
            nextActionCodes = List.copyOf(nextActionCodes);
        }
    }
}
