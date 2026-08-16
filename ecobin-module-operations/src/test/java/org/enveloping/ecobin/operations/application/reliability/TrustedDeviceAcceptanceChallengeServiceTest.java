package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.time.LocalDateTime;
import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

class TrustedDeviceAcceptanceChallengeServiceTest {

    @Test
    void evidenceMustMatchTheExactChallengeBagSnapshot() {
        byte[] digest = HexFormat.of().parseHex("ab".repeat(32));

        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 4L, digest))
                .isTrue();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 5L, digest))
                .isFalse();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 4L,
                        HexFormat.of().parseHex("cd".repeat(32))))
                .isFalse();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(0L, null, 0L, digest))
                .isFalse();
    }

    @Test
    void expiredBlockedChallengeIsCancelledWithoutTouchingLiveWork() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        TrustedDeviceAcceptanceChallengeService service =
                new TrustedDeviceAcceptanceChallengeService(jdbc);
        LocalDateTime now = LocalDateTime.of(
                2026, 8, 16, 12, 30, 0, 123_000_000);

        service.cancelExpiredBlocked(7L, now);

        var sql = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(jdbc).update(
                sql.capture(), eq(now), eq(now), eq(7L), eq(now));
        String normalized = sql.getValue().replaceAll("\\s+", " ");
        assertThat(normalized)
                .contains("task_type = 'REQUEST_DEVICE_ACCEPTANCE'")
                .contains("state = 'BLOCKED'")
                .contains("lease_token IS NULL")
                .contains("'$.expiresAt'")
                .doesNotContain("state IN ('PENDING', 'BLOCKED')");
    }
}
