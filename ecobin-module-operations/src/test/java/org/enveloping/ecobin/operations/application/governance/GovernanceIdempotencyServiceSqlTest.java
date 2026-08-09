package org.enveloping.ecobin.operations.application.governance;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class GovernanceIdempotencyServiceSqlTest {

    @Test
    void duplicateClaimTouchesOnlyRuntimeMutableTimestamp() {
        String sql = GovernanceIdempotencyService.CLAIM_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "ON DUPLICATE KEY UPDATE",
                        "UPDATED_AT = UPDATED_AT")
                .doesNotContain(
                        "OPERATION_UID = VALUES(OPERATION_UID)",
                        "OPERATION_UID = NEW.OPERATION_UID");
    }
}
