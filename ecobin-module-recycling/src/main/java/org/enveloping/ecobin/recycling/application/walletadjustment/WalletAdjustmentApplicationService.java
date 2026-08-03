package org.enveloping.ecobin.recycling.application.walletadjustment;

import org.enveloping.ecobin.framework.web.TargetWebAuditRequestContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.funds.api.command.AdjustWalletCommand;
import org.enveloping.ecobin.funds.api.port.WalletAdjustmentPort;
import org.enveloping.ecobin.funds.api.result.WalletAdjustmentResult;
import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentScopeRef;
import org.enveloping.ecobin.identity.api.port.WalletAdjustmentAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.WalletAdjustmentAuthorizationQuery;
import org.enveloping.ecobin.identity.api.result.AuthorizedWalletAdjustment;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.AdjustWalletRequest;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.WalletAdjustmentView;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Clock;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class WalletAdjustmentApplicationService {

    private static final String ACTION = "wallet.adjust";

    private final WalletAdjustmentAuthorizationPort authorization;
    private final WalletAdjustmentPort funds;
    private final JdbcTemplate jdbc;
    private final Clock clock;

    @Autowired
    public WalletAdjustmentApplicationService(
            WalletAdjustmentAuthorizationPort authorization,
            WalletAdjustmentPort funds,
            JdbcTemplate jdbc) {
        this(authorization, funds, jdbc, Clock.systemUTC());
    }

    WalletAdjustmentApplicationService(
            WalletAdjustmentAuthorizationPort authorization,
            WalletAdjustmentPort funds,
            JdbcTemplate jdbc,
            Clock clock) {
        this.authorization = authorization;
        this.funds = funds;
        this.jdbc = jdbc;
        this.clock = clock;
    }

    @Transactional(
            isolation = Isolation.READ_COMMITTED,
            readOnly = false)
    public WalletAdjustmentView adjust(
            boolean platformPath,
            String tenantCode,
            String organizationCode,
            UUID organizationUserUid,
            UUID operationUid,
            AdjustWalletRequest request) {
        requireUuidV4(operationUid);
        Normalized normalized = normalize(request);
        AuthorizedWalletAdjustment authorized = authorization.authorize(
                new WalletAdjustmentAuthorizationQuery(
                        platformPath,
                        tenantCode,
                        organizationCode,
                        organizationUserUid));
        long threshold = lockCurrentThreshold(
                authorized.configurationScopeRef());
        TargetWebAuditRequestContext.describe(
                ACTION, organizationUserUid.toString());
        Instant occurredAt = clock.instant()
                .truncatedTo(ChronoUnit.MILLIS);
        WalletAdjustmentResult result = funds.adjust(
                new AdjustWalletCommand(
                        operationUid,
                        authorized.fundsTargetRef(),
                        authorized.platformActor(),
                        authorized.principalUid(),
                        authorized.sessionUid(),
                        authorized.actorDisplayName(),
                        normalized.deltaCent(),
                        normalized.expectedWalletVersion(),
                        normalized.reason(),
                        threshold,
                        occurredAt));
        return view(result);
    }

    private long lockCurrentThreshold(WalletAdjustmentScopeRef scopeRef) {
        return scopeRef.withScopeOnce((tenantId, organizationId) -> {
            List<Long> rows = jdbc.query("""
                            SELECT config.open_balance_floor_cent
                            FROM rec_organization_delivery_config_head head
                            JOIN rec_organization_delivery_config config
                              ON config.tenant_id = head.tenant_id
                             AND config.organization_id = head.organization_id
                             AND config.id = head.current_config_id
                             AND config.version_no = head.current_version_no
                            WHERE head.tenant_id = ?
                              AND head.organization_id = ?
                            FOR UPDATE
                            """,
                    (rs, ignored) -> rs.getLong(
                            "open_balance_floor_cent"),
                    tenantId,
                    organizationId);
            if (rows.size() != 1) {
                throw new IllegalStateException(
                        "recycling invariant violated: current delivery "
                                + "configuration is missing");
            }
            return rows.getFirst();
        });
    }

    private static Normalized normalize(AdjustWalletRequest request) {
        if (request == null) {
            throw validation(Map.of("request", "请求体不能为空"));
        }
        long delta = parseCent(request.deltaYuan());
        if (delta == 0) {
            throw validation(Map.of("deltaYuan", "调整差额不能为 0"));
        }
        if (request.expectedWalletVersion() == null
                || request.expectedWalletVersion() < 0) {
            throw validation(Map.of(
                    "expectedWalletVersion",
                    "钱包版本不能为空且不能为负数"));
        }
        String reason = request.reason() == null
                || request.reason().isBlank()
                ? null : request.reason().trim();
        if (reason != null && reason.length() > 500) {
            throw validation(Map.of("reason", "原因最多 500 个字符"));
        }
        return new Normalized(
                delta, request.expectedWalletVersion(), reason);
    }

    private static long parseCent(String value) {
        try {
            if (value == null || value.isBlank()) {
                throw new NumberFormatException();
            }
            return new BigDecimal(value.trim())
                    .setScale(2, RoundingMode.UNNECESSARY)
                    .movePointRight(2)
                    .longValueExact();
        } catch (ArithmeticException | NumberFormatException exception) {
            throw validation(Map.of(
                    "deltaYuan",
                    "必须是精确到分的有符号人民币元字符串"));
        }
    }

    private static WalletAdjustmentView view(
            WalletAdjustmentResult result) {
        return new WalletAdjustmentView(
                result.adjustmentUid(),
                result.entryUid(),
                money(result.deltaCent()),
                money(result.availableBalanceBeforeCent()),
                money(result.availableBalanceAfterCent()),
                result.walletVersion(),
                result.deliveryGate(),
                result.activeWithdrawalEffect().name(),
                result.occurredAt());
    }

    private static String money(long cent) {
        return BigDecimal.valueOf(cent, 2).toPlainString();
    }

    private static void requireUuidV4(UUID value) {
        if (value == null || value.version() != 4) {
            throw validation(Map.of(
                    "Idempotency-Key", "必须是 UUIDv4"));
        }
    }

    private static TargetApiException validation(
            Map<String, Object> details) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                "钱包调整请求不符合接口契约",
                false,
                details);
    }

    private record Normalized(
            long deltaCent,
            long expectedWalletVersion,
            String reason) {
    }
}
