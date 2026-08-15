package org.enveloping.ecobin.operations.application.governance;

import org.enveloping.ecobin.framework.idempotency.GlobalOperationIdempotencyPort;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationBinding;
import org.enveloping.ecobin.framework.idempotency.GlobalOperationResult;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class GovernanceIdempotencyServiceSqlTest {

    @Test
    void providesTheFrameworkGlobalOperationPort() {
        assertThat(GlobalOperationIdempotencyPort.class)
                .isAssignableFrom(GovernanceIdempotencyService.class);
    }

    @Test
    void claimAndSuccessMustJoinAnExistingBusinessTransaction()
            throws NoSuchMethodException {
        Transactional claim = GovernanceIdempotencyService.class
                .getMethod("claim", GlobalOperationBinding.class)
                .getAnnotation(Transactional.class);
        Transactional succeed = GovernanceIdempotencyService.class
                .getMethod("succeed", java.util.UUID.class,
                        GlobalOperationResult.class)
                .getAnnotation(Transactional.class);

        assertThat(claim.propagation()).isEqualTo(Propagation.MANDATORY);
        assertThat(succeed.propagation()).isEqualTo(Propagation.MANDATORY);
    }

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

    @Test
    void anInProgressClaimChecksTheHistoricalSuccessAuditKey() {
        assertThat(GovernanceIdempotencyService.LEGACY_SUCCESS_SQL
                .toUpperCase(Locale.ROOT))
                .contains(
                        "FROM OPS_AUDIT_LOG",
                        "SUCCEEDED_OPERATION_UID = ?");
    }
}
