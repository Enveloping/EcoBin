package org.enveloping.ecobin.recycling.application.walletadjustment;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class WalletAdjustmentApplicationServiceSqlTest {

    @Test
    void adjustmentLocksOnlyMutableDeliveryConfigurationHead() {
        String sql = WalletAdjustmentApplicationService
                .LOCK_CURRENT_DELIVERY_CONFIG_HEAD_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "FROM REC_ORGANIZATION_DELIVERY_CONFIG_HEAD",
                        "FOR UPDATE")
                .doesNotContain(
                        "JOIN REC_ORGANIZATION_DELIVERY_CONFIG");
    }

    @Test
    void immutableDeliveryConfigurationUsesPlainRead() {
        String sql = WalletAdjustmentApplicationService
                .LOAD_CURRENT_DELIVERY_THRESHOLD_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains("FROM REC_ORGANIZATION_DELIVERY_CONFIG")
                .doesNotContain("FOR UPDATE");
    }
}
