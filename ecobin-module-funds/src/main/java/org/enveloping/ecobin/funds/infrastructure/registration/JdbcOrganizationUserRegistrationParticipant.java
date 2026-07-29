package org.enveloping.ecobin.funds.infrastructure.registration;

import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationParticipant;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

/**
 * Creates the funds-owned wallet records for a newly registered organization
 * user inside the identity registration transaction.
 */
@Component
public class JdbcOrganizationUserRegistrationParticipant
        implements OrganizationUserRegistrationParticipant {

    private final JdbcTemplate jdbc;

    public JdbcOrganizationUserRegistrationParticipant(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void initializeWallet(OrganizationUserRegistrationCommand command) {
        command.walletOwnerRef().writeForeignKeyTo(
                (tenantKey, organizationKey, userKey) -> {
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
                });
    }
}
