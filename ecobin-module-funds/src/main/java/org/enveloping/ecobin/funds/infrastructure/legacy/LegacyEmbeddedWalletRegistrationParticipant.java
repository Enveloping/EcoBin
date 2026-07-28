package org.enveloping.ecobin.funds.infrastructure.legacy;

import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import javax.sql.DataSource;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Locale;
import java.util.UUID;

/**
 * 首次机构用户注册的单一 funds 参与者。
 *
 * <p>目标纪元写入 funds 自有 {@code fund_user_wallet}；旧恢复单元仍只消费引用并沿用
 * {@code sys_user} 的内嵌零余额默认值。两条路径都不回查 identity 私表。</p>
 */
@Component
public class LegacyEmbeddedWalletRegistrationParticipant
        implements OrganizationUserRegistrationParticipant {

    private final JdbcTemplate jdbc;
    private final DataSource dataSource;
    private volatile Boolean targetWalletTablePresent;

    public LegacyEmbeddedWalletRegistrationParticipant(
            JdbcTemplate jdbc,
            DataSource dataSource) {
        this.jdbc = jdbc;
        this.dataSource = dataSource;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void initializeWallet(OrganizationUserRegistrationCommand command) {
        command.walletOwnerRef().writeForeignKeyTo((tenantKey, organizationKey, userKey) -> {
            if (targetWalletTablePresent()) {
                jdbc.update("""
                                INSERT INTO fund_user_wallet (
                                    wallet_uid, tenant_id, organization_id,
                                    organization_user_id,
                                    available_balance_cent,
                                    frozen_withdrawal_cent,
                                    last_entry_sequence_no,
                                    delivery_gate_state,
                                    delivery_gate_threshold_snapshot_cent,
                                    delivery_gate_trigger_entry_id,
                                    delivery_gate_latched_at,
                                    lock_version, created_at, updated_at
                                ) VALUES (
                                    ?, ?, ?, ?,
                                    0, 0, 0, 'OPEN',
                                    NULL, NULL, NULL,
                                    0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                )
                                """,
                        UUID.randomUUID().toString(),
                        tenantKey,
                        organizationKey,
                        userKey);
                jdbc.update("""
                                INSERT INTO fund_organization_wallet_entry_counter (
                                    organization_id, tenant_id,
                                    last_visibility_sequence_no,
                                    lock_version, updated_at
                                ) VALUES (?, ?, 0, 0, UTC_TIMESTAMP(3))
                                ON DUPLICATE KEY UPDATE
                                    last_visibility_sequence_no =
                                        fund_organization_wallet_entry_counter
                                            .last_visibility_sequence_no
                                """,
                        organizationKey,
                        tenantKey);
                return;
            }
            if (tenantKey != organizationKey) {
                throw new IllegalStateException(
                        "legacy tenant and organization keys must match");
            }
            if (userKey <= 0) {
                throw new IllegalStateException(
                        "organization user must already be persisted");
            }
        });
    }

    private boolean targetWalletTablePresent() {
        Boolean cached = targetWalletTablePresent;
        if (cached != null) {
            return cached;
        }
        synchronized (this) {
            if (targetWalletTablePresent == null) {
                targetWalletTablePresent = inspectTargetWalletTable();
            }
            return targetWalletTablePresent;
        }
    }

    private boolean inspectTargetWalletTable() {
        try (var connection = dataSource.getConnection()) {
            String catalog = connection.getCatalog();
            try (ResultSet tables = connection.getMetaData().getTables(
                    catalog, null, "%", new String[]{"TABLE"})) {
                while (tables.next()) {
                    String name = tables.getString("TABLE_NAME");
                    if ("FUND_USER_WALLET".equals(
                            name.toUpperCase(Locale.ROOT))) {
                        return true;
                    }
                }
            }
            return false;
        } catch (SQLException exception) {
            throw new IllegalStateException(
                    "cannot inspect target wallet persistence", exception);
        }
    }
}
