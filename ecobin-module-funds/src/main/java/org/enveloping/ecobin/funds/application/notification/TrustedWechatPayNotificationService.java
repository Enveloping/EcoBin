package org.enveloping.ecobin.funds.application.notification;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;
import org.enveloping.ecobin.funds.api.port.WechatPayNotificationPort;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.application.withdrawal.WithdrawalApplicationService;
import org.enveloping.ecobin.funds.application.authorization.MerchantTransferAuthorizationApplicationService;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class TrustedWechatPayNotificationService
        implements WechatPayNotificationPort {

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;
    private final RechargeApplicationService recharge;
    private final WithdrawalApplicationService withdrawal;
    private final MerchantTransferAuthorizationApplicationService authorization;

    public TrustedWechatPayNotificationService(
            JdbcTemplate jdbc,
            ObjectMapper mapper,
            RechargeApplicationService recharge,
            WithdrawalApplicationService withdrawal,
            MerchantTransferAuthorizationApplicationService authorization) {
        this.jdbc = jdbc;
        this.mapper = mapper;
        this.recharge = recharge;
        this.withdrawal = withdrawal;
        this.authorization = authorization;
    }

    @Override
    public TrustedInboxScopeResolver scopeResolver(
            String messageKind,
            String mchid,
            String externalOrderNo) {
        return writer -> {
            List<Scope> rows = switch (messageKind) {
                case PAYMENT_KIND -> jdbc.query("""
                        SELECT r.tenant_id, r.organization_id
                        FROM fund_wechat_payment p
                        JOIN fund_recharge_order r ON r.id = p.recharge_order_id
                        WHERE p.out_trade_no = ? AND p.mchid_snapshot = ?
                        """, (rs, ignored) -> new Scope(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id")),
                        externalOrderNo, mchid);
                case TRANSFER_KIND -> jdbc.query("""
                        SELECT w.tenant_id, w.organization_id
                        FROM fund_wechat_transfer t
                        JOIN fund_withdrawal_order w ON w.id = t.withdrawal_order_id
                        WHERE t.out_bill_no = ? AND t.mchid_snapshot = ?
                        """, (rs, ignored) -> new Scope(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id")),
                        externalOrderNo, mchid);
                case TRANSFER_AUTHORIZATION_KIND -> jdbc.query("""
                        SELECT a.tenant_id, a.organization_id
                        FROM fund_wechat_transfer_authorization a
                        WHERE a.out_authorization_no = ?
                          AND a.mchid_snapshot = ?
                        """, (rs, ignored) -> new Scope(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id")),
                        externalOrderNo, mchid);
                default -> throw new IllegalArgumentException(
                        "unsupported WeChat Pay notification kind");
            };
            if (rows.size() != 1) {
                throw new IllegalArgumentException(
                        "WeChat Pay notification cannot resolve one business scope");
            }
            Scope scope = rows.getFirst();
            writer.organization(scope.tenantId(), scope.organizationId());
        };
    }

    @Override
    public boolean apply(
            TrustedOrganizationInboxRef sourceInbox,
            long sourceTaskAttemptId,
            String messageKind,
            int schemaVersion,
            String normalizedPayload) {
        if (schemaVersion != 1) {
            throw new IllegalArgumentException(
                    "unsupported WeChat Pay notification schema version");
        }
        JsonNode payload;
        try {
            payload = mapper.readTree(normalizedPayload);
        } catch (Exception failure) {
            throw new IllegalArgumentException(
                    "invalid normalized WeChat Pay notification", failure);
        }
        return sourceInbox.use((inboxId, tenantId, organizationId) ->
                switch (messageKind) {
                    case PAYMENT_KIND -> recharge.applyTrustedNotification(
                            inboxId, sourceTaskAttemptId,
                            tenantId, organizationId, payload);
                    case TRANSFER_KIND -> withdrawal.applyTrustedNotification(
                            inboxId, sourceTaskAttemptId,
                            tenantId, organizationId, payload);
                    case TRANSFER_AUTHORIZATION_KIND ->
                            authorization.applyTrustedNotification(
                                    inboxId, sourceTaskAttemptId,
                                    tenantId, organizationId, payload);
                    default -> throw new IllegalArgumentException(
                            "unsupported WeChat Pay notification kind");
                });
    }

    private record Scope(long tenantId, long organizationId) {
    }
}
