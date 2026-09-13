package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class CleanBagRecoveryBaselineRetrySqlTest {

    @Test
    void lateOldAttemptCannotDisplaceANewerPendingAttempt() {
        String sql = TrustedOrangePiRuntimeFactService
                .MARK_CLEAN_BAG_RECOVERY_BASELINE_REQUIRED_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("status = 'baseline_pending'")
                .contains("not exists")
                .contains("active_attempt.clean_bag_recovery_id = ?")
                .contains("active_attempt.status = 'pending'");
    }
}
