package org.enveloping.ecobin.funds.application.startdelivery;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JdbcDeliveryWalletQualificationQueryRepositoryTest {

    @Test
    void readOnlyQualificationUsesOnlyTheFundsWalletTableWithoutLocking() {
        String sql = JdbcDeliveryWalletQualificationQueryRepository
                .FIND_CURRENT_WALLET_SQL
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ")
                .trim();

        assertTrue(sql.contains("from fund_user_wallet"));
        assertTrue(sql.contains("available_balance_cent"));
        assertTrue(sql.contains("delivery_gate_state"));
        assertFalse(sql.contains("for update"));
        assertFalse(sql.contains("iam_organization_user"));
        assertFalse(sql.contains(" join "));
    }
}
