package org.enveloping.ecobin.device.application.factory;

import org.enveloping.ecobin.device.api.port.BagCodeAdmissionPort;
import org.enveloping.ecobin.device.api.port.TrustedDeviceAcceptanceChallengePort;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.CorrectFactoryBagRequest;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.FactoryAcceptanceView;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.FactoryBagSlotView;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.InstallFactoryBagRequest;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.VerifyFactoryBagRequest;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.audit.SuccessfulAudit;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationClaim;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationDigests;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationResult;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.FactoryMiniappAuthorizationPort;
import org.enveloping.ecobin.identity.api.result.AuthorizedFactoryOperatorIdentity;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/** Factory-only bag admission before a real device may start acceptance. */
@Service
public class FactoryAcceptanceService {

    private final JdbcTemplate jdbc;
    private final ObjectMapper objectMapper;
    private final BagCodeAdmissionPort bagAdmission;
    private final FactoryMiniappAuthorizationPort authorization;
    private final AuditPort auditPort;
    private final TrustedDeviceAcceptanceChallengePort acceptanceChallenges;
    private final GlobalOperationIdempotencyPort idempotency;

    public FactoryAcceptanceService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper,
            BagCodeAdmissionPort bagAdmission,
            FactoryMiniappAuthorizationPort authorization,
            AuditPort auditPort,
            TrustedDeviceAcceptanceChallengePort acceptanceChallenges,
            GlobalOperationIdempotencyPort idempotency) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.bagAdmission = bagAdmission;
        this.authorization = authorization;
        this.auditPort = auditPort;
        this.acceptanceChallenges = acceptanceChallenges;
        this.idempotency = idempotency;
    }

    @Transactional(readOnly = true)
    public FactoryAcceptanceView detail(String deviceCode) {
        authorization.requireCapability(
                FactoryMiniappAuthorizationPort.ACCEPTANCE_READ);
        return view(requireDeviceCode(deviceCode));
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryAcceptanceView install(
            UUID operationUid,
            String deviceCode,
            InstallFactoryBagRequest request) {
        requireOperationUid(operationUid);
        String normalizedDeviceCode = requireDeviceCode(deviceCode);
        if (request == null || request.portNo() == null) {
            throw invalid("投口编号和袋码不能为空");
        }
        String bagCode = authenticate(request.bagCode());
        int portNo = request.portNo();
        byte[] requestSha256 = fingerprint(
                "INSTALL", normalizedDeviceCode, portNo, bagCode, null);
        AuthorizedFactoryOperatorIdentity actor = authorization
                .requireCapability(
                        FactoryMiniappAuthorizationPort.BAG_INSTALL);
        FactoryAcceptanceView replay = replay(
                operationUid,
                requestSha256,
                normalizedDeviceCode,
                portNo,
                "device.factory-bag.install",
                actor);
        if (replay != null) {
            return replay;
        }
        GlobalOperationClaim claim = idempotency.claim(binding(
                operationUid,
                actor,
                "device.factory-bag.install",
                normalizedDeviceCode,
                portNo,
                requestSha256));
        if (claim.replay()) {
            return requireReplay(
                    operationUid,
                    requestSha256,
                    normalizedDeviceCode,
                    portNo,
                    "device.factory-bag.install",
                    actor);
        }
        Asset asset = lockAsset(normalizedDeviceCode);
        requireMutable(asset, portNo);
        Label label = lockLabel(bagCode);
        requireLabelUnused(label, bagCode);
        if (currentBag(asset.id(), portNo) != null) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_PORT_OCCUPIED",
                    "该投口已经登记初始袋，如扫码错误请使用更正操作");
        }

        LocalDateTime now = databaseNow();
        try {
            actor.persistenceRef().writeForeignKeyTo(factoryOperatorId -> {
                jdbc.update("""
                                INSERT INTO rec_bag_label_claim (
                                    claim_uid, label_item_id, claim_kind,
                                    asset_id, port_no,
                                    claimed_by_factory_operator_id,
                                    claimed_at, released_at,
                                    release_reason, created_at
                                ) VALUES (?, ?, 'FACTORY_INSTALLATION', ?, ?, ?,
                                          ?, NULL, NULL, ?)
                                """,
                        UUID.randomUUID().toString(),
                        label.id(),
                        asset.id(),
                        portNo,
                        factoryOperatorId,
                        now,
                        now);
                jdbc.update("""
                                INSERT INTO dev_factory_installed_bag (
                                    asset_id, port_no, bag_code,
                                    installation_source,
                                    installed_by_factory_operator_id,
                                    label_item_id, tare_status,
                                    last_failure_code, installed_at,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, 'FACTORY_MINIAPP', ?, ?,
                                          'PENDING', NULL, ?, ?, ?)
                                """,
                        asset.id(),
                        portNo,
                        bagCode,
                        factoryOperatorId,
                        label.id(),
                        now,
                        now,
                        now);
                insertChange(
                        operationUid,
                        requestSha256,
                        asset.id(),
                        portNo,
                        "INSTALLED",
                        null,
                        bagCode,
                        factoryOperatorId,
                        null,
                        now);
                appendAudit(
                        operationUid,
                        actor,
                        factoryOperatorId,
                        "device.factory-bag.install",
                        normalizedDeviceCode,
                        portNo,
                        null,
                        bagCode,
                        null,
                        requestSha256,
                        now);
            });
        } catch (DuplicateKeyException collision) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_ALREADY_CLAIMED",
                    "该袋码已被使用，或投口已由另一请求登记");
        }
        long revision = advanceFactoryBagGeneration(asset.id(), now);
        idempotency.succeed(operationUid, new GlobalOperationResult(
                asset.assetUid(), "INSTALLED", revision));
        return view(normalizedDeviceCode);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryAcceptanceView verify(
            UUID operationUid,
            String deviceCode,
            int portNo,
            VerifyFactoryBagRequest request) {
        requireOperationUid(operationUid);
        String normalizedDeviceCode = requireDeviceCode(deviceCode);
        if (request == null) {
            throw invalid("补扫确认请求不能为空");
        }
        String bagCode = authenticate(request.bagCode());
        byte[] requestSha256 = fingerprint(
                "VERIFY", normalizedDeviceCode, portNo, bagCode, null);
        AuthorizedFactoryOperatorIdentity actor = authorization
                .requireCapability(
                        FactoryMiniappAuthorizationPort.BAG_INSTALL);
        FactoryAcceptanceView replay = replay(
                operationUid,
                requestSha256,
                normalizedDeviceCode,
                portNo,
                "device.factory-bag.verify",
                actor);
        if (replay != null) {
            return replay;
        }
        GlobalOperationClaim claim = idempotency.claim(binding(
                operationUid,
                actor,
                "device.factory-bag.verify",
                normalizedDeviceCode,
                portNo,
                requestSha256));
        if (claim.replay()) {
            return requireReplay(
                    operationUid,
                    requestSha256,
                    normalizedDeviceCode,
                    portNo,
                    "device.factory-bag.verify",
                    actor);
        }
        Asset asset = lockAsset(normalizedDeviceCode);
        requireMutable(asset, portNo);
        CurrentBag previous = currentBag(asset.id(), portNo);
        if (previous == null) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_NOT_INSTALLED",
                    "该投口没有可补扫确认的存量初始袋");
        }
        if (!"PLATFORM_CREATE".equals(previous.installationSource())) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_ALREADY_VERIFIED",
                    "该投口已经完成厂家扫码，如扫错请使用更正袋码");
        }
        if (!previous.bagCode().equals(bagCode)) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_LEGACY_CODE_MISMATCH",
                    "实体袋码与平台旧记录不同，请使用更正袋码并填写原因");
        }
        Label label = lockLabel(bagCode);
        requireLabelUnused(label, bagCode);

        LocalDateTime now = databaseNow();
        try {
            actor.persistenceRef().writeForeignKeyTo(factoryOperatorId -> {
                insertActiveClaim(
                        label.id(), asset.id(), portNo,
                        factoryOperatorId, now);
                requireSingle(jdbc.update("""
                                UPDATE dev_factory_installed_bag
                                SET installation_source = 'FACTORY_MINIAPP',
                                    installed_by_factory_operator_id = ?,
                                    label_item_id = ?, tare_status = 'PENDING',
                                    last_failure_code = NULL,
                                    installed_at = ?, updated_at = ?
                                WHERE asset_id = ? AND port_no = ?
                                  AND bag_code = ?
                                  AND installation_source = 'PLATFORM_CREATE'
                                """,
                        factoryOperatorId,
                        label.id(),
                        now,
                        now,
                        asset.id(),
                        portNo,
                        bagCode), "verify legacy factory bag");
                insertChange(
                        operationUid,
                        requestSha256,
                        asset.id(),
                        portNo,
                        "VERIFIED",
                        bagCode,
                        bagCode,
                        factoryOperatorId,
                        null,
                        now);
                appendAudit(
                        operationUid,
                        actor,
                        factoryOperatorId,
                        "device.factory-bag.verify",
                        normalizedDeviceCode,
                        portNo,
                        bagCode,
                        bagCode,
                        null,
                        requestSha256,
                        now);
            });
        } catch (DuplicateKeyException collision) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_ALREADY_CLAIMED",
                    "该袋码已被其他设备或投口使用");
        }
        long revision = advanceFactoryBagGeneration(asset.id(), now);
        idempotency.succeed(operationUid, new GlobalOperationResult(
                asset.assetUid(), "VERIFIED", revision));
        return view(normalizedDeviceCode);
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public FactoryAcceptanceView correct(
            UUID operationUid,
            String deviceCode,
            int portNo,
            CorrectFactoryBagRequest request) {
        requireOperationUid(operationUid);
        String normalizedDeviceCode = requireDeviceCode(deviceCode);
        if (request == null) {
            throw invalid("更正请求不能为空");
        }
        String reason = required(request.reason(), 500, "更正原因");
        String bagCode = authenticate(request.bagCode());
        byte[] requestSha256 = fingerprint(
                "CORRECT", normalizedDeviceCode, portNo, bagCode, reason);
        AuthorizedFactoryOperatorIdentity actor = authorization
                .requireCapability(
                        FactoryMiniappAuthorizationPort.BAG_CORRECT);
        FactoryAcceptanceView replay = replay(
                operationUid,
                requestSha256,
                normalizedDeviceCode,
                portNo,
                "device.factory-bag.correct",
                actor);
        if (replay != null) {
            return replay;
        }
        GlobalOperationClaim claim = idempotency.claim(binding(
                operationUid,
                actor,
                "device.factory-bag.correct",
                normalizedDeviceCode,
                portNo,
                requestSha256));
        if (claim.replay()) {
            return requireReplay(
                    operationUid,
                    requestSha256,
                    normalizedDeviceCode,
                    portNo,
                    "device.factory-bag.correct",
                    actor);
        }
        Asset asset = lockAsset(normalizedDeviceCode);
        requireMutable(asset, portNo);
        CurrentBag previous = currentBag(asset.id(), portNo);
        if (previous == null) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_NOT_INSTALLED",
                    "该投口尚未登记初始袋，不能执行更正");
        }
        if (previous.bagCode().equals(bagCode)) {
            throw invalid("更正后的袋码必须与当前袋码不同");
        }
        Label replacement = lockLabel(bagCode);
        requireLabelUnused(replacement, bagCode);

        LocalDateTime now = databaseNow();
        try {
            actor.persistenceRef().writeForeignKeyTo(factoryOperatorId -> {
                if (previous.labelItemId() != null) {
                    int released = jdbc.update("""
                                    UPDATE rec_bag_label_claim
                                    SET released_at = ?, release_reason = ?
                                    WHERE label_item_id = ?
                                      AND asset_id = ? AND port_no = ?
                                      AND released_at IS NULL
                                    """,
                            now,
                            reason,
                            previous.labelItemId(),
                            asset.id(),
                            portNo);
                    requireSingle(released, "release factory label claim");
                } else if (!"PLATFORM_CREATE".equals(
                        previous.installationSource())) {
                    throw new IllegalStateException(
                            "verified factory bag has no active label claim");
                }
                insertActiveClaim(
                        replacement.id(), asset.id(), portNo,
                        factoryOperatorId, now);
                int updated = jdbc.update("""
                                UPDATE dev_factory_installed_bag
                                SET bag_code = ?,
                                    installation_source = 'FACTORY_MINIAPP',
                                    installed_by_factory_operator_id = ?,
                                    label_item_id = ?, tare_status = 'PENDING',
                                    last_failure_code = NULL,
                                    installed_at = ?, updated_at = ?
                                WHERE asset_id = ? AND port_no = ?
                                  AND bag_code = ?
                                """,
                        bagCode,
                        factoryOperatorId,
                        replacement.id(),
                        now,
                        now,
                        asset.id(),
                        portNo,
                        previous.bagCode());
                requireSingle(updated, "correct factory bag");
                insertChange(
                        operationUid,
                        requestSha256,
                        asset.id(),
                        portNo,
                        "CORRECTED",
                        previous.bagCode(),
                        bagCode,
                        factoryOperatorId,
                        reason,
                        now);
                appendAudit(
                        operationUid,
                        actor,
                        factoryOperatorId,
                        "device.factory-bag.correct",
                        normalizedDeviceCode,
                        portNo,
                        previous.bagCode(),
                        bagCode,
                        reason,
                        requestSha256,
                        now);
            });
        } catch (DuplicateKeyException collision) {
            throw conflict(
                    "DEVICE.FACTORY_BAG_ALREADY_CLAIMED",
                    "更正后的袋码已被其他设备或投口使用");
        }
        long revision = advanceFactoryBagGeneration(asset.id(), now);
        idempotency.succeed(operationUid, new GlobalOperationResult(
                asset.assetUid(), "CORRECTED", revision));
        return view(normalizedDeviceCode);
    }

    private FactoryAcceptanceView replay(
            UUID operationUid,
            byte[] requestSha256,
            String deviceCode,
            int portNo,
            String actionCode,
            AuthorizedFactoryOperatorIdentity actor) {
        byte[] prior = jdbc.query("""
                        SELECT request_sha256
                        FROM dev_factory_installed_bag_change
                        WHERE change_uid = ?
                        """,
                (rs, ignored) -> rs.getBytes("request_sha256"),
                operationUid.toString()).stream().findFirst().orElse(null);
        if (prior == null) {
            return null;
        }
        if (!MessageDigest.isEqual(prior, requestSha256)) {
            throw conflict(
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
        SuccessfulAudit audit = auditPort.findSuccessful(operationUid)
                .orElseThrow(() -> new IllegalStateException(
                        "factory bag change has no successful audit"));
        if (audit.actorKind() != AuditActorKind.FACTORY_OPERATOR
                || audit.scopeKind() != AuditScopeKind.PLATFORM
                || !actionCode.equals(audit.actionCode())
                || !"device-factory-bag".equals(audit.targetType())
                || !(deviceCode + ":" + portNo).equals(
                audit.targetStableKey())) {
            throw idempotencyConflict();
        }
        boolean[] sameActor = {false};
        actor.persistenceRef().writeForeignKeyTo(factoryOperatorId ->
                sameActor[0] = Objects.equals(
                        audit.factoryOperatorId(), factoryOperatorId));
        if (!sameActor[0]) {
            throw idempotencyConflict();
        }
        return view(deviceCode);
    }

    private FactoryAcceptanceView requireReplay(
            UUID operationUid,
            byte[] requestSha256,
            String deviceCode,
            int portNo,
            String actionCode,
            AuthorizedFactoryOperatorIdentity actor) {
        FactoryAcceptanceView replay = replay(
                operationUid,
                requestSha256,
                deviceCode,
                portNo,
                actionCode,
                actor);
        if (replay == null) {
            throw new IllegalStateException(
                    "completed factory idempotency claim has no change fact");
        }
        return replay;
    }

    private FactoryAcceptanceView view(String deviceCode) {
        Asset asset = jdbc.query("""
                        SELECT id, asset_uid, device_public_code, hardware_sn,
                               expected_port_count, acceptance_status,
                               lifecycle_status, tenant_id,
                               factory_bag_revision
                        FROM dev_device_asset
                        WHERE device_public_code = ?
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("asset_uid")),
                        rs.getString("device_public_code"),
                        rs.getString("hardware_sn"),
                        rs.getInt("expected_port_count"),
                        rs.getString("acceptance_status"),
                        rs.getString("lifecycle_status"),
                        (Long) rs.getObject("tenant_id"),
                        rs.getLong("factory_bag_revision")),
                deviceCode).stream().findFirst()
                .orElseThrow(FactoryAcceptanceService::notFound);
        List<FactoryBagSlotView> bags = jdbc.query("""
                        SELECT bag.port_no, bag.bag_code,
                               bag.installation_source, bag.installed_at,
                               CASE
                                   WHEN bag.installation_source =
                                        'LEGACY_GRANDFATHERED' THEN 1
                                   WHEN bag.installation_source =
                                        'FACTORY_MINIAPP'
                                     AND bag.installed_by_factory_operator_id
                                         IS NOT NULL
                                     AND bag.label_item_id IS NOT NULL
                                     AND EXISTS (
                                         SELECT 1
                                         FROM rec_bag_label_claim claim
                                         WHERE claim.label_item_id =
                                               bag.label_item_id
                                           AND claim.asset_id = bag.asset_id
                                           AND claim.port_no = bag.port_no
                                           AND claim.released_at IS NULL
                                     ) THEN 1
                                   ELSE 0
                               END AS factory_verified
                        FROM dev_factory_installed_bag bag
                        WHERE bag.asset_id = ?
                        ORDER BY bag.port_no
                        """,
                (rs, ignored) -> new FactoryBagSlotView(
                        rs.getInt("port_no"),
                        rs.getString("bag_code"),
                        rs.getString("installation_source"),
                        verificationStatus(
                                rs.getString("installation_source"),
                                rs.getBoolean("factory_verified")),
                        rs.getObject("installed_at", LocalDateTime.class)
                                .toInstant(ZoneOffset.UTC)),
                asset.id());
        boolean complete = allFactoryBagsVerified(
                asset.expectedPortCount(), bags);
        return new FactoryAcceptanceView(
                asset.deviceCode(),
                asset.hardwareSn(),
                asset.expectedPortCount(),
                asset.acceptanceStatus(),
                complete,
                complete
                        && "NORMAL".equals(asset.lifecycleStatus())
                        && asset.tenantId() == null
                        && !"PASSED".equals(asset.acceptanceStatus()),
                bags);
    }

    private Asset lockAsset(String deviceCode) {
        return jdbc.query("""
                        SELECT id, asset_uid, device_public_code, hardware_sn,
                               expected_port_count, acceptance_status,
                               lifecycle_status, tenant_id,
                               factory_bag_revision
                        FROM dev_device_asset
                        WHERE device_public_code = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new Asset(
                        rs.getLong("id"),
                        UUID.fromString(rs.getString("asset_uid")),
                        rs.getString("device_public_code"),
                        rs.getString("hardware_sn"),
                        rs.getInt("expected_port_count"),
                        rs.getString("acceptance_status"),
                        rs.getString("lifecycle_status"),
                        (Long) rs.getObject("tenant_id"),
                        rs.getLong("factory_bag_revision")),
                deviceCode).stream().findFirst()
                .orElseThrow(FactoryAcceptanceService::notFound);
    }

    private void requireMutable(Asset asset, int portNo) {
        if (portNo < 1 || portNo > asset.expectedPortCount()) {
            throw invalid("投口编号超出该设备的实际投口范围");
        }
        if (!"NORMAL".equals(asset.lifecycleStatus())
                || asset.tenantId() != null
                || "PASSED".equals(asset.acceptanceStatus())) {
            throw conflict(
                    "DEVICE.FACTORY_ACCEPTANCE_LOCKED",
                    "设备已通过验收、已分配租户、已禁用或已报废，不能修改厂家初始袋");
        }
    }

    private Label lockLabel(String bagCode) {
        // Issued labels are immutable printing facts.  A shared lock keeps the
        // row alive while the unique active-claim constraint arbitrates
        // concurrent attempts to bind the same label.
        return jdbc.query("""
                        SELECT id, bag_code
                        FROM rec_bag_label_item
                        WHERE bag_code = ?
                        FOR SHARE
                        """,
                (rs, ignored) -> new Label(
                        rs.getLong("id"), rs.getString("bag_code")),
                bagCode).stream().findFirst()
                .orElseThrow(() -> new TargetApiException(
                        422,
                        "RECYCLING.BAG_LABEL_NOT_ISSUED",
                        "袋码虽通过防伪校验，但不是平台已签发的标签"));
    }

    private void requireLabelUnused(Label label, String bagCode) {
        Integer claimed = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_bag_label_claim
                        WHERE label_item_id = ? AND released_at IS NULL
                        """,
                Integer.class,
                label.id());
        Integer materialized = jdbc.queryForObject("""
                        SELECT COUNT(*) FROM rec_bag WHERE bag_code = ?
                        """,
                Integer.class,
                bagCode);
        if ((claimed != null && claimed > 0)
                || (materialized != null && materialized > 0)) {
            throw conflict(
                    "RECYCLING.BAG_LABEL_ALREADY_USED",
                    "该袋码已经绑定到其他设备、投口或周转袋");
        }
    }

    private CurrentBag currentBag(long assetId, int portNo) {
        return jdbc.query("""
                        SELECT bag_code, installation_source, label_item_id
                        FROM dev_factory_installed_bag
                        WHERE asset_id = ? AND port_no = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new CurrentBag(
                        rs.getString("bag_code"),
                        rs.getString("installation_source"),
                        (Long) rs.getObject("label_item_id")),
                assetId,
                portNo).stream().findFirst().orElse(null);
    }

    static String verificationStatus(
            String installationSource,
            boolean verified) {
        if ("LEGACY_GRANDFATHERED".equals(installationSource)) {
            return "LEGACY_GRANDFATHERED";
        }
        return verified ? "FACTORY_VERIFIED" : "NEEDS_FACTORY_SCAN";
    }

    static boolean allFactoryBagsVerified(
            int expectedPortCount,
            List<FactoryBagSlotView> bags) {
        return bags.size() == expectedPortCount
                && bags.stream().allMatch(bag -> !"NEEDS_FACTORY_SCAN"
                        .equals(bag.verificationStatus()));
    }

    private void insertActiveClaim(
            long labelItemId,
            long assetId,
            int portNo,
            long factoryOperatorId,
            LocalDateTime now) {
        jdbc.update("""
                        INSERT INTO rec_bag_label_claim (
                            claim_uid, label_item_id, claim_kind,
                            asset_id, port_no,
                            claimed_by_factory_operator_id,
                            claimed_at, released_at,
                            release_reason, created_at
                        ) VALUES (?, ?, 'FACTORY_INSTALLATION', ?, ?, ?,
                                  ?, NULL, NULL, ?)
                        """,
                UUID.randomUUID().toString(),
                labelItemId,
                assetId,
                portNo,
                factoryOperatorId,
                now,
                now);
    }

    private long advanceFactoryBagGeneration(
            long assetId,
            LocalDateTime now) {
        List<String> canonicalBags = jdbc.query("""
                        SELECT port_no, bag_code
                        FROM dev_factory_installed_bag
                        WHERE asset_id = ?
                        ORDER BY port_no
                        """,
                (rs, ignored) -> rs.getInt("port_no") + ":"
                        + rs.getString("bag_code"),
                assetId);
        byte[] digest = factoryBagSetSha256(canonicalBags);
        acceptanceChallenges.cancelOutstanding(assetId, now);
        requireSingle(jdbc.update("""
                        UPDATE dev_device_asset
                        SET factory_bag_revision = factory_bag_revision + 1,
                            factory_bag_set_sha256 = ?,
                            acceptance_status = 'PENDING',
                            accepted_at = NULL,
                            acceptance_evidence_sha256 = NULL,
                            last_acceptance_evaluated_at = NULL,
                            acceptance_failure_json = NULL,
                            control_version = control_version + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                digest,
                now,
                assetId), "advance factory bag generation");
        Long revision = jdbc.queryForObject("""
                        SELECT factory_bag_revision
                        FROM dev_device_asset
                        WHERE id = ?
                        """,
                Long.class,
                assetId);
        if (revision == null) {
            throw new IllegalStateException(
                    "factory bag generation disappeared");
        }
        return revision;
    }

    private static GlobalOperationBinding binding(
            UUID operationUid,
            AuthorizedFactoryOperatorIdentity actor,
            String actionCode,
            String deviceCode,
            int portNo,
            byte[] requestSha256) {
        return new GlobalOperationBinding(
                operationUid,
                "FACTORY_OPERATOR",
                actor.factoryOperatorUid(),
                GlobalOperationDigests.platformScope(),
                actionCode,
                "device-factory-bag",
                deviceCode + ":" + portNo,
                HexFormat.of().formatHex(requestSha256));
    }

    static byte[] factoryBagSetSha256(List<String> canonicalBags) {
        return sha256(String.join("\n", canonicalBags)
                .getBytes(StandardCharsets.US_ASCII));
    }

    private void insertChange(
            UUID operationUid,
            byte[] requestSha256,
            long assetId,
            int portNo,
            String kind,
            String previousCode,
            String currentCode,
            long factoryOperatorId,
            String reason,
            LocalDateTime now) {
        jdbc.update("""
                        INSERT INTO dev_factory_installed_bag_change (
                            change_uid, request_sha256, asset_id, port_no,
                            change_kind, previous_bag_code,
                            current_bag_code, factory_operator_id,
                            reason, occurred_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                operationUid.toString(),
                requestSha256,
                assetId,
                portNo,
                kind,
                previousCode,
                currentCode,
                factoryOperatorId,
                reason,
                now,
                now);
    }

    private void appendAudit(
            UUID operationUid,
            AuthorizedFactoryOperatorIdentity actor,
            long factoryOperatorId,
            String action,
            String deviceCode,
            int portNo,
            String previousCode,
            String currentCode,
            String reason,
            byte[] requestSha256,
            LocalDateTime now) {
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("fingerprint", HexFormat.of().formatHex(requestSha256));
        summary.put("portNo", portNo);
        summary.put("previousBagCodeSha256", bagCodeHash(previousCode));
        summary.put("currentBagCodeSha256", bagCodeHash(currentCode));
        auditPort.append(new AuditEntry(
                UUID.randomUUID(),
                UUID.randomUUID(),
                operationUid,
                AuditScopeKind.PLATFORM,
                null,
                null,
                AuditActorKind.FACTORY_OPERATOR,
                null,
                factoryOperatorId,
                null,
                null,
                null,
                actor.displayName(),
                action,
                "device-factory-bag",
                deviceCode + ":" + portNo,
                "MINIAPP_FACTORY",
                "SUCCEEDED",
                actor.sessionUid(),
                reason,
                objectMapper.writeValueAsString(summary),
                now.toInstant(ZoneOffset.UTC)));
    }

    private byte[] fingerprint(
            String action,
            String deviceCode,
            int portNo,
            String bagCode,
            String reason) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schemaVersion", 1);
        value.put("action", action);
        value.put("deviceCode", deviceCode);
        value.put("portNo", portNo);
        value.put("bagCode", bagCode);
        value.put("reason", reason);
        return sha256(objectMapper.writeValueAsBytes(value));
    }

    private String authenticate(String rawCode) {
        return bagAdmission.authenticate(rawCode)
                .orElseThrow(() -> new TargetApiException(
                        422,
                        "RECYCLING.BAG_LABEL_AUTHENTICATION_FAILED",
                        "袋码未通过 EB1 防伪校验"))
                .value();
    }

    private static String bagCodeHash(String value) {
        return value == null ? null : HexFormat.of().formatHex(sha256(
                value.getBytes(StandardCharsets.US_ASCII)));
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }

    private static String requireDeviceCode(String value) {
        String normalized = value == null ? "" : value.trim();
        if (!normalized.matches("^Dv_[A-Za-z0-9_-]{24,61}$")) {
            throw invalid("设备二维码中的公开码无效");
        }
        return normalized;
    }

    private static String required(
            String value,
            int maximumLength,
            String field) {
        String normalized = value == null ? "" : value.trim();
        if (normalized.isEmpty() || normalized.length() > maximumLength) {
            throw invalid(field + "不能为空且不能超过 "
                    + maximumLength + " 个字符");
        }
        return normalized;
    }

    private static void requireOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw invalid("Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static void requireSingle(int rows, String action) {
        if (rows != 1) {
            throw new IllegalStateException(
                    action + " affected " + rows + " rows");
        }
    }

    private LocalDateTime databaseNow() {
        return jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(
                400, "COMMON.INVALID_REQUEST", message);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "设备不存在");
    }

    private static TargetApiException conflict(
            String code,
            String message) {
        return new TargetApiException(409, code, message);
    }

    private static TargetApiException idempotencyConflict() {
        return conflict(
                "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                "相同操作标识已绑定到不同请求或厂家操作员");
    }

    private record Asset(
            long id,
            UUID assetUid,
            String deviceCode,
            String hardwareSn,
            int expectedPortCount,
            String acceptanceStatus,
            String lifecycleStatus,
            Long tenantId,
            long factoryBagRevision) {
    }

    private record Label(long id, String bagCode) {
    }

    private record CurrentBag(
            String bagCode,
            String installationSource,
            Long labelItemId) {
    }
}
