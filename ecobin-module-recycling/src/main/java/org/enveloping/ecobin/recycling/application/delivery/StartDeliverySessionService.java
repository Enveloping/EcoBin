package org.enveloping.ecobin.recycling.application.delivery;

import org.enveloping.ecobin.device.api.command.StartDeliveryDeviceCommand;
import org.enveloping.ecobin.device.api.port.StartDeliveryDeviceParticipationPort;
import org.enveloping.ecobin.device.api.result.StartDeliveryDeviceResult;
import org.enveloping.ecobin.device.api.value.DeliveryRuleSnapshot;
import org.enveloping.ecobin.funds.api.command.StartDeliveryWalletQualificationCommand;
import org.enveloping.ecobin.funds.api.port.StartDeliveryWalletQualificationPort;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartDeliveryIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedDeliveryOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappDeliveryScope;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliverySessionAccepted;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;

/**
 * 开始投递的外层协调事务，由 recycling 模块拥有。
 *
 * <p>一次扫码会话只在云端授权一次：本服务按既定锁序确认身份、机构规则、钱包资格和
 * 设备业务事实，再让 device 模块写入会话、整机占位、命令和可靠任务。用户在设备上的
 * “继续投递/结束投递”属于已授权会话内的本地动作，不会再次进入 HTTP 用例。</p>
 */
@Service
public class StartDeliverySessionService {

    private static final long RECOMMENDED_POLL_AFTER_MS = 1_000;
    private static final String ACTION = "delivery.start";
    private static final String TARGET_TYPE = "DELIVERY_SESSION";

    private final JdbcTemplate jdbc;
    private final StartDeliveryIdentityParticipationPort identity;
    private final StartDeliveryWalletQualificationPort wallet;
    private final StartDeliveryDeviceParticipationPort device;
    private final AuditPort audit;
    private final ObjectMapper objectMapper;

    public StartDeliverySessionService(
            JdbcTemplate jdbc,
            StartDeliveryIdentityParticipationPort identity,
            StartDeliveryWalletQualificationPort wallet,
            StartDeliveryDeviceParticipationPort device,
            AuditPort audit,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.identity = identity;
        this.wallet = wallet;
        this.device = device;
        this.audit = audit;
        this.objectMapper = objectMapper;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeliverySessionAccepted start(
            UUID operationUid,
            String deviceCode,
            int portNo) {
        validateOperationUid(operationUid);
        // 锁序是并发协议的一部分：身份作用域 → 机构投递规则 → 机构用户 → 钱包 → 设备。
        // 参与模块都使用 MANDATORY 加入当前事务，任一检查失败会整体回滚。
        LockedMiniappDeliveryScope scope =
                identity.lockCurrentMiniappScope();
        DeliveryRuleSnapshot deliveryRule =
                scope.organizationScopeRef()
                        .withOrganizationScopeOnce(
                                this::lockCurrentDeliveryRule);
        LockedDeliveryOrganizationUser user =
                identity.lockCurrentOrganizationUser(scope);
        String fingerprint = fingerprint(
                user.organizationUserUid().value(),
                deviceCode,
                portNo);
        return user.auditActorRef().withAuditActorOnce(
                (tenantId, organizationId, organizationUserId) ->
                        startOrReplay(
                                operationUid,
                                deviceCode,
                                portNo,
                                deliveryRule,
                                user,
                                fingerprint,
                                tenantId,
                                organizationId,
                                organizationUserId));
    }

    private DeliverySessionAccepted startOrReplay(
            UUID operationUid,
            String deviceCode,
            int portNo,
            DeliveryRuleSnapshot deliveryRule,
            LockedDeliveryOrganizationUser user,
            String fingerprint,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        Optional<SuccessfulAudit> previous =
                audit.findSuccessful(operationUid);
        if (previous.isPresent()) {
            // 相同操作者、路径参数和幂等键返回第一次成功响应，不创建第二个物理会话。
            return replay(
                    previous.orElseThrow(),
                    fingerprint,
                    tenantId,
                    organizationId,
                    organizationUserId);
        }
        // 钱包此时只决定“能否开始新投递”，不会产生返现明细；金额要等订单审核后入账。
        wallet.lockAndRequireEligible(
                new StartDeliveryWalletQualificationCommand(
                        user.walletOwnerRef(),
                        deliveryRule.openBalanceFloorCent()));
        // device 参与者在同一事务内冻结设备事实并登记可靠下发任务。
        StartDeliveryDeviceResult accepted = device.start(
                new StartDeliveryDeviceCommand(
                        operationUid,
                        deviceCode,
                        portNo,
                        user.deliverySessionUserRef(),
                        deliveryRule));
        String statusUrl = "/api/v1/miniapp/delivery-sessions/"
                + accepted.sessionUid();
        DeliverySessionAccepted response =
                new DeliverySessionAccepted(
                operationUid,
                accepted.sessionUid(),
                accepted.sessionUid(),
                "ACTIVE",
                "START_QUEUED",
                accepted.authorizationExpiresAt(),
                statusUrl,
                RECOMMENDED_POLL_AFTER_MS,
                List.of("WAIT"));
        Map<String, Object> safeSummary =
                new LinkedHashMap<>();
        safeSummary.put("fingerprint", fingerprint);
        safeSummary.put("response", response);
        try {
            // 成功审计同时占据全局幂等槽。只有审计和前述设备事实一起提交，202 才可返回。
            audit.append(new AuditEntry(
                    UUID.randomUUID(),
                    UUID.randomUUID(),
                    operationUid,
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
                    accepted.sessionUid().toString(),
                    "MINIAPP_USER",
                    "SUCCEEDED",
                    user.loginSessionUid().value(),
                    null,
                    writeJson(safeSummary),
                    Instant.now()));
        } catch (DuplicateKeyException exception) {
            /*
             * The audit row owns the global successful-operation slot. If two
             * requests race with the same Idempotency-Key, the database lets
             * only one commit. Converting that exact insert failure to the
             * public conflict keeps the losing outer transaction atomic:
             * its session, occupancy, command and task are all rolled back.
             */
            throw idempotencyConflict();
        }
        return response;
    }

    private DeliverySessionAccepted replay(
            SuccessfulAudit previous,
            String fingerprint,
            long tenantId,
            long organizationId,
            long organizationUserId) {
        JsonNode summary = readJson(
                previous.safeChangeSummaryJson());
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
            DeliverySessionAccepted response =
                    objectMapper.treeToValue(
                            summary.path("response"),
                            DeliverySessionAccepted.class);
            if (response == null
                    || !response.sessionUid().toString().equals(
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

    private DeliveryRuleSnapshot lockCurrentDeliveryRule(
            long tenantId,
            long organizationId) {
        // 先锁配置头，再读取其指向的不可变版本，保证本次会话冻结的是同一套规则快照。
        var heads = jdbc.query("""
                        SELECT current_config_id, current_version_no
                        FROM rec_organization_delivery_config_head
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new DeliveryRuleHead(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no")),
                tenantId,
                organizationId);
        if (heads.size() != 1) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于投递的当前规则");
        }
        DeliveryRuleHead head = heads.getFirst();
        var rows = jdbc.query("""
                        SELECT version_no,
                               LOWER(HEX(content_sha256))
                                   AS content_sha256,
                               open_balance_floor_cent,
                               max_review_abs_weight_g
                        FROM rec_organization_delivery_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND id = ?
                          AND version_no = ?
                        """,
                (rs, ignored) -> new DeliveryRuleSnapshot(
                        rs.getLong("version_no"),
                        rs.getString("content_sha256"),
                        rs.getLong("open_balance_floor_cent"),
                        rs.getLong("max_review_abs_weight_g")),
                tenantId,
                organizationId,
                head.configId(),
                head.version());
        if (rows.size() != 1) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.CONFIGURATION_UNAVAILABLE",
                    "机构还没有可用于投递的当前规则");
        }
        return rows.getFirst();
    }

    private record DeliveryRuleHead(
            long configId,
            long version) {
    }

    private static void validateOperationUid(UUID value) {
        if (value == null || value.version() != 4 || value.variant() != 2) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_IDEMPOTENCY_KEY",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "start-delivery audit summary cannot be encoded",
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
            UUID organizationUserUid,
            String deviceCode,
            int portNo) {
        try {
            MessageDigest digest =
                    MessageDigest.getInstance("SHA-256");
            digest.update(
                    organizationUserUid.toString()
                            .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(
                    deviceCode.trim()
                            .getBytes(StandardCharsets.UTF_8));
            digest.update((byte) 0);
            digest.update(
                    Integer.toString(portNo)
                            .getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of()
                    .formatHex(digest.digest());
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable",
                    exception);
        }
    }

    private static TargetApiException idempotencyConflict() {
        return new TargetApiException(
                409,
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "该 Idempotency-Key 已用于另一项投递请求");
    }
}
