package org.enveloping.ecobin.funds.application.startdelivery;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertTrue;

class JdbcStartDeliveryWalletLockRepositoryTest {

    @Test
    void qualificationReadsAuthoritativeWalletFieldsUnderLock() {
        String sql = JdbcStartDeliveryWalletLockRepository
                .LOCK_WALLET_SQL
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();

        assertTrue(sql.contains("from fund_user_wallet"));
        assertTrue(sql.contains("available_balance_cent"));
        assertTrue(sql.contains("delivery_gate_state"));
        assertTrue(sql.contains("tenant_id = ?"));
        assertTrue(sql.contains("organization_id = ?"));
        assertTrue(sql.contains("organization_user_id = ?"));
        assertTrue(sql.endsWith("for update"));
    }
}
