package org.enveloping.ecobin.funds.infrastructure.registration;

import org.enveloping.ecobin.identity.api.command.OrganizationBootstrapCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationBootstrapParticipant;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

/**
 * 在机构创建事务内同步建立 funds 自有的零余额机构出款账户。
 */
@Component("fundsOrganizationBootstrapParticipant")
public class JdbcOrganizationBootstrapParticipant
        implements OrganizationBootstrapParticipant {

    private final JdbcTemplate jdbc;

    public JdbcOrganizationBootstrapParticipant(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void initializeOrganization(
            OrganizationBootstrapCommand command) {
        command.persistenceRef().writeForeignKeyTo(
                (tenantKey, organizationKey) -> {
                    jdbc.update("""
                        INSERT INTO fund_organization_payout_account (
                            account_uid, tenant_id, organization_id,
                            available_payout_cent,
                            frozen_withdrawal_cent,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 0, 0, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                        UUID.randomUUID().toString(),
                        tenantKey,
                        organizationKey);
                    jdbc.update("""
                            INSERT INTO fund_organization_withdraw_config (
                                tenant_id, organization_id, version_no,
                                content_sha256, hard_limit_cent,
                                manual_min_cent, manual_max_cent,
                                manual_review_free_threshold_cent,
                                publication_source,
                                published_by_staff_account_id,
                                published_at, created_at
                            ) VALUES (?, ?, 1, ?, 1000, 10, 1000, 0,
                                      'SYSTEM', NULL,
                                      UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))
                            """, tenantKey, organizationKey,
                            sha256("hard=1000;min=10;max=1000;reviewFree=0"));
                    Long configId = jdbc.queryForObject("""
                            SELECT id
                            FROM fund_organization_withdraw_config
                            WHERE tenant_id = ? AND organization_id = ?
                              AND version_no = 1
                            """, Long.class, tenantKey, organizationKey);
                    jdbc.update("""
                            INSERT INTO fund_organization_withdraw_config_head (
                                organization_id, tenant_id, current_config_id,
                                current_version_no, lock_version,
                                switched_at, updated_at
                            ) VALUES (?, ?, ?, 1, 0,
                                      UTC_TIMESTAMP(3), UTC_TIMESTAMP(3))
                            """, organizationKey, tenantKey, configId);
                });
    }

    private static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception failure) {
            throw new IllegalStateException("SHA-256 unavailable", failure);
        }
    }
}
