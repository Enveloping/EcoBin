package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.result.DeviceAcceptanceChallengeConsumeResult;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.sql.ResultSet;
import java.time.LocalDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TrustedDeviceAcceptanceChallengeServiceTest {

    private static final UUID COMMAND_UID = UUID.fromString(
            "40000000-0000-4000-8000-000000000001");
    private static final UUID CHALLENGE_UID = UUID.fromString(
            "50000000-0000-4000-8000-000000000001");
    private static final LocalDateTime RECEIVED_AT = LocalDateTime.of(
            2026, 8, 30, 4, 0, 0, 123_000_000);

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
    void cancelledChallengeWithMatchingSnapshotIsClassifiedAsLateEvidence() {
        JdbcTemplate jdbc = challengeJdbc(
                "CANCELLED", 7L, "ab".repeat(32));
        TrustedDeviceAcceptanceChallengeService service =
                new TrustedDeviceAcceptanceChallengeService(jdbc);

        DeviceAcceptanceChallengeConsumeResult result = service.consume(
                13L,
                COMMAND_UID,
                CHALLENGE_UID,
                7L,
                HexFormat.of().parseHex("ab".repeat(32)),
                RECEIVED_AT);

        assertThat(result).isEqualTo(
                DeviceAcceptanceChallengeConsumeResult.CANCELLED);
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void cancelledChallengeStillRejectsDifferentFactoryBagSnapshot() {
        JdbcTemplate jdbc = challengeJdbc(
                "CANCELLED", 7L, "ab".repeat(32));
        TrustedDeviceAcceptanceChallengeService service =
                new TrustedDeviceAcceptanceChallengeService(jdbc);

        assertThatThrownBy(() -> service.consume(
                13L,
                COMMAND_UID,
                CHALLENGE_UID,
                7L,
                HexFormat.of().parseHex("cd".repeat(32)),
                RECEIVED_AT))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("factory bag snapshot");

        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void matchingDoneChallengeRemainsConsumedWithoutAnotherUpdate() {
        JdbcTemplate jdbc = challengeJdbc(
                "DONE", 7L, "ab".repeat(32));
        TrustedDeviceAcceptanceChallengeService service =
                new TrustedDeviceAcceptanceChallengeService(jdbc);

        DeviceAcceptanceChallengeConsumeResult result = service.consume(
                13L,
                COMMAND_UID,
                CHALLENGE_UID,
                7L,
                HexFormat.of().parseHex("ab".repeat(32)),
                RECEIVED_AT);

        assertThat(result).isEqualTo(
                DeviceAcceptanceChallengeConsumeResult.CONSUMED);
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void matchingPendingOrBlockedChallengeIsConsumedExactlyOnce() {
        for (String state : List.of("PENDING", "BLOCKED")) {
            JdbcTemplate jdbc = challengeJdbc(
                    state, 7L, "ab".repeat(32));
            TrustedDeviceAcceptanceChallengeService service =
                    new TrustedDeviceAcceptanceChallengeService(jdbc);

            DeviceAcceptanceChallengeConsumeResult result = service.consume(
                    13L,
                    COMMAND_UID,
                    CHALLENGE_UID,
                    7L,
                    HexFormat.of().parseHex("ab".repeat(32)),
                    RECEIVED_AT);

            assertThat(result).isEqualTo(
                    DeviceAcceptanceChallengeConsumeResult.CONSUMED);
            var sql = org.mockito.ArgumentCaptor.forClass(String.class);
            verify(jdbc).update(
                    sql.capture(),
                    eq(RECEIVED_AT),
                    eq(RECEIVED_AT),
                    eq(91L));
            assertThat(sql.getValue().replaceAll("\\s+", " "))
                    .contains("SET state = 'DONE'")
                    .contains("WHERE id = ?");
        }
    }

    @Test
    void missingExactChallengeStillRejectsEvidence() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                anyString(), any(RowMapper.class), any(Object[].class)))
                .thenReturn(List.of());
        TrustedDeviceAcceptanceChallengeService service =
                new TrustedDeviceAcceptanceChallengeService(jdbc);

        assertThatThrownBy(() -> service.consume(
                13L,
                COMMAND_UID,
                CHALLENGE_UID,
                7L,
                HexFormat.of().parseHex("ab".repeat(32)),
                RECEIVED_AT))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("does not match a platform challenge");
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

    @SuppressWarnings("unchecked")
    private static JdbcTemplate challengeJdbc(
            String state,
            long factoryBagRevision,
            String factoryBagSetSha256) {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        when(jdbc.query(
                anyString(), any(RowMapper.class), any(Object[].class)))
                .thenAnswer(invocation -> {
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    ResultSet resultSet = mock(ResultSet.class);
                    when(resultSet.getLong("id")).thenReturn(91L);
                    when(resultSet.getString("state")).thenReturn(state);
                    when(resultSet.getLong("wake_version")).thenReturn(3L);
                    when(resultSet.getLong("factory_bag_revision"))
                            .thenReturn(factoryBagRevision);
                    when(resultSet.getString("factory_bag_set_sha256"))
                            .thenReturn(factoryBagSetSha256);
                    return List.of(mapper.mapRow(resultSet, 0));
                });
        when(jdbc.update(anyString(), any(Object[].class)))
                .thenReturn(1);
        return jdbc;
    }
}
