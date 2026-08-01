package org.enveloping.ecobin.funds.application.deliveryrevision;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertTrue;

class JdbcDeliveryRevisionDeltaRepositoryTest {

    @Test
    void everyBusinessRootReadUsesAnAuthoritativeRowLock() {
        assertLock(
                JdbcDeliveryRevisionDeltaRepository.LOCK_WALLET_SQL,
                "fund_user_wallet");
        assertLock(
                JdbcDeliveryRevisionDeltaRepository
                        .LOCK_ORGANIZATION_COUNTER_SQL,
                "fund_organization_wallet_entry_counter");
        assertLock(
                JdbcDeliveryRevisionDeltaRepository
                        .LOCK_ACTIVE_WITHDRAWAL_SQL,
                "fund_active_withdrawal");
        assertLock(
                JdbcDeliveryRevisionDeltaRepository
                        .LOCK_WITHDRAWAL_ORDER_SQL,
                "fund_withdrawal_order");
    }

    @Test
    void walletEntryIsRevisionSourcedAndProjectionWritesAreVersioned() {
        String insert = normalize(
                JdbcDeliveryRevisionDeltaRepository
                        .INSERT_WALLET_ENTRY_SQL);
        assertTrue(insert.contains(
                "insert into fund_user_wallet_entry"));
        assertTrue(insert.contains("delivery_revision_id"));
        assertTrue(insert.contains("available_before_cent"));
        assertTrue(insert.contains("available_after_cent"));
        assertTrue(insert.contains("frozen_delta_cent"));

        String counter = normalize(
                JdbcDeliveryRevisionDeltaRepository
                        .ADVANCE_ORGANIZATION_COUNTER_SQL);
        assertTrue(counter.contains(
                "last_visibility_sequence_no = ?"));
        assertTrue(counter.contains(
                "lock_version = lock_version + 1"));
        assertTrue(counter.contains("lock_version = ?"));

        String wallet = normalize(
                JdbcDeliveryRevisionDeltaRepository
                        .UPDATE_WALLET_SQL);
        assertTrue(wallet.contains(
                "available_balance_cent = ?"));
        assertTrue(wallet.contains(
                "last_entry_sequence_no = ?"));
        assertTrue(wallet.contains(
                "delivery_gate_trigger_entry_id = ?"));
        assertTrue(wallet.contains(
                "lock_version = lock_version + 1"));

        String withdrawal = normalize(
                JdbcDeliveryRevisionDeltaRepository
                        .UPDATE_WITHDRAWAL_ORDER_SQL);
        assertTrue(withdrawal.contains(
                "negative_balance_pause = ?"));
        assertTrue(withdrawal.contains(
                "post_boundary_risk = ?"));
        assertTrue(withdrawal.contains(
                "lock_version = lock_version + 1"));
    }

    private static void assertLock(String sql, String table) {
        String normalized = normalize(sql);
        assertTrue(normalized.contains("from " + table));
        assertTrue(normalized.contains("tenant_id = ?"));
        assertTrue(normalized.contains("organization_id = ?"));
        assertTrue(normalized.endsWith("for update"));
    }

    private static String normalize(String sql) {
        return sql.toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();
    }
}
