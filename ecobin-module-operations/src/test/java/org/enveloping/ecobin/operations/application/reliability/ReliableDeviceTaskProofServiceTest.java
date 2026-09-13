package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import tools.jackson.databind.ObjectMapper;

import java.sql.ResultSet;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ReliableDeviceTaskProofServiceTest {

    private static final UUID COMMAND_UID = UUID.fromString(
            "8a000000-0000-4000-8000-000000000005");
    private static final String HARDWARE_SN = "SN-URL-PROOF-0001";
    private static final String URL_SHA256 = "1".repeat(64);
    private static final LocalDateTime NOW = LocalDateTime.of(
            2026, 9, 14, 4, 12, 13, 456_000_000);

    @Test
    void appliedCompletesOnlyTheMatchingPlatformUrlTask() throws Exception {
        JdbcTemplate jdbc = taskJdbc("PENDING", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(NOW);
        when(jdbc.update(
                contains("SET state = 'DONE'"), any(Object[].class)))
                .thenReturn(1);

        service(jdbc).applyDeviceEntryUrlApplicationResult(
                COMMAND_UID,
                HARDWARE_SN,
                URL_SHA256,
                "APPLIED",
                null);

        verifyLookupIdentity(jdbc);
        verify(jdbc).queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        verify(jdbc).update(
                contains("WHERE id = ?"),
                eq(NOW),
                eq(NOW),
                eq(91L));
    }

    @Test
    void failedBlocksTheTaskWithTheReportedHardwareFault() throws Exception {
        JdbcTemplate jdbc = taskJdbc("PENDING", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));
        when(jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                .thenReturn(NOW);
        when(jdbc.update(
                contains("SET state = 'BLOCKED'"),
                any(Object[].class))).thenReturn(1);

        service(jdbc).applyDeviceEntryUrlApplicationResult(
                COMMAND_UID,
                HARDWARE_SN,
                URL_SHA256,
                "FAILED",
                "BUSY");

        verifyLookupIdentity(jdbc);
        verify(jdbc).queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        verify(jdbc).update(
                contains("'DEVICE_ENTRY_URL_APPLICATION_FAILED'"),
                eq(NOW),
                eq("MCU/HMI URL application failed: BUSY"),
                eq(NOW),
                eq(91L));
    }

    @Test
    void repeatedMatchingAppliedResultDoesNotMutateDoneTask()
            throws Exception {
        JdbcTemplate jdbc = taskJdbc("DONE", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));

        service(jdbc).applyDeviceEntryUrlApplicationResult(
                COMMAND_UID,
                HARDWARE_SN,
                URL_SHA256,
                "APPLIED",
                null);

        verify(jdbc, never()).queryForObject(
                anyString(), eq(LocalDateTime.class));
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void repeatedMatchingFailedResultDoesNotMutateBlockedTask()
            throws Exception {
        JdbcTemplate jdbc = taskJdbc(
                "BLOCKED",
                "DEVICE_ENTRY_URL_APPLICATION_FAILED",
                "MCU/HMI URL application failed: BUSY",
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));

        service(jdbc).applyDeviceEntryUrlApplicationResult(
                COMMAND_UID,
                HARDWARE_SN,
                URL_SHA256,
                "FAILED",
                "BUSY");

        verify(jdbc, never()).queryForObject(
                anyString(), eq(LocalDateTime.class));
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void contradictoryResultCannotRewriteAnExistingTerminalOutcome()
            throws Exception {
        JdbcTemplate doneJdbc = taskJdbc("DONE", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));
        JdbcTemplate blockedJdbc = taskJdbc(
                "BLOCKED",
                "DEVICE_ENTRY_URL_APPLICATION_FAILED",
                "MCU/HMI URL application failed: BUSY",
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));

        assertThatThrownBy(() -> service(doneJdbc)
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID,
                        HARDWARE_SN,
                        URL_SHA256,
                        "FAILED",
                        "BUSY"))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("conflicts with completed task");
        assertThatThrownBy(() -> service(blockedJdbc)
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID,
                        HARDWARE_SN,
                        URL_SHA256,
                        "APPLIED",
                        null))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("conflicts with blocked task");
    }

    @Test
    void validLateResultDoesNotRewriteCancelledTask() throws Exception {
        JdbcTemplate jdbc = taskJdbc("CANCELLED", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));

        service(jdbc).applyDeviceEntryUrlApplicationResult(
                COMMAND_UID,
                HARDWARE_SN,
                URL_SHA256,
                "APPLIED",
                null);

        verify(jdbc, never()).queryForObject(
                anyString(), eq(LocalDateTime.class));
        verify(jdbc, never()).update(anyString(), any(Object[].class));
    }

    @Test
    void resultMustMatchTheFrozenCommandIdentityDeviceAndDigest()
            throws Exception {
        List<String> invalidEnvelopes = List.of(
                frozenEnvelope(UUID.fromString(
                        "8a000000-0000-4000-8000-000000000006"),
                        HARDWARE_SN,
                        URL_SHA256),
                frozenEnvelope(COMMAND_UID, "SN-OTHER", URL_SHA256),
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, "2".repeat(64)),
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256)
                        .replace("\"DEVICE_ASSET\"", "\"DEVICE_COMMAND\""),
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256)
                        .replace("\"SYNC_DEVICE_ENTRY_URL\"",
                                "\"REQUEST_DEVICE_ACCEPTANCE\""));

        for (String envelope : invalidEnvelopes) {
            JdbcTemplate jdbc = taskJdbc(
                    "PENDING", null, null, envelope);

            assertThatThrownBy(() -> service(jdbc)
                    .applyDeviceEntryUrlApplicationResult(
                            COMMAND_UID,
                            HARDWARE_SN,
                            URL_SHA256,
                            "APPLIED",
                            null))
                    .isInstanceOf(ReliableTaskInvariantException.class)
                    .hasMessageContaining("differs from the frozen command");
            verify(jdbc, never()).update(
                    anyString(), any(Object[].class));
        }
    }

    @Test
    @SuppressWarnings("unchecked")
    void missingOrAmbiguousTaskIsRejectedBeforeAnyMutation()
            throws Exception {
        JdbcTemplate missing = mock(JdbcTemplate.class);
        when(missing.query(
                anyString(), any(RowMapper.class), any(Object[].class)))
                .thenReturn(List.of());
        JdbcTemplate ambiguous = taskJdbc(
                "PENDING", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256), 2);

        for (JdbcTemplate jdbc : List.of(missing, ambiguous)) {
            assertThatThrownBy(() -> service(jdbc)
                    .applyDeviceEntryUrlApplicationResult(
                            COMMAND_UID,
                            HARDWARE_SN,
                            URL_SHA256,
                            "APPLIED",
                            null))
                    .isInstanceOf(ReliableTaskInvariantException.class)
                    .hasMessageContaining("did not resolve one platform task");
            verify(jdbc, never()).update(
                    anyString(), any(Object[].class));
        }
    }

    @Test
    void concurrentStateChangeThatPreventsTheTerminalWriteIsRejected()
            throws Exception {
        JdbcTemplate appliedJdbc = taskJdbc(
                "PENDING", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));
        JdbcTemplate failedJdbc = taskJdbc(
                "PENDING", null, null,
                frozenEnvelope(COMMAND_UID, HARDWARE_SN, URL_SHA256));
        for (JdbcTemplate jdbc : List.of(appliedJdbc, failedJdbc)) {
            when(jdbc.queryForObject(
                    "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class))
                    .thenReturn(NOW);
            when(jdbc.update(anyString(), any(Object[].class)))
                    .thenReturn(0);
        }

        assertThatThrownBy(() -> service(appliedJdbc)
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID,
                        HARDWARE_SN,
                        URL_SHA256,
                        "APPLIED",
                        null))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("could not complete its task");
        assertThatThrownBy(() -> service(failedJdbc)
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID,
                        HARDWARE_SN,
                        URL_SHA256,
                        "FAILED",
                        "BUSY"))
                .isInstanceOf(ReliableTaskInvariantException.class)
                .hasMessageContaining("could not block its task");
    }

    @Test
    void statusAndFaultCodeMustDescribeOneCoherentResult() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ReliableDeviceTaskProofService service = service(jdbc);

        assertThatThrownBy(() -> service
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID, HARDWARE_SN, URL_SHA256,
                        "APPLIED", "BUSY"))
                .isInstanceOf(ReliableTaskInvariantException.class);
        assertThatThrownBy(() -> service
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID, HARDWARE_SN, URL_SHA256,
                        "FAILED", null))
                .isInstanceOf(ReliableTaskInvariantException.class);
        assertThatThrownBy(() -> service
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID, HARDWARE_SN, URL_SHA256,
                        "FAILED", " "))
                .isInstanceOf(ReliableTaskInvariantException.class);
        assertThatThrownBy(() -> service
                .applyDeviceEntryUrlApplicationResult(
                        COMMAND_UID, HARDWARE_SN, URL_SHA256,
                        "UNKNOWN", null))
                .isInstanceOf(ReliableTaskInvariantException.class);
        verify(jdbc, never()).query(
                anyString(), any(RowMapper.class), any(Object[].class));
    }

    private static ReliableDeviceTaskProofService service(
            JdbcTemplate jdbc) {
        return new ReliableDeviceTaskProofService(
                jdbc, new ObjectMapper());
    }

    private static void verifyLookupIdentity(JdbcTemplate jdbc) {
        var sql = org.mockito.ArgumentCaptor.forClass(String.class);
        verify(jdbc).query(
                sql.capture(),
                any(RowMapper.class),
                eq(HARDWARE_SN),
                eq(HARDWARE_SN),
                eq(COMMAND_UID.toString()));
        assertThat(sql.getValue().replaceAll("\\s+", " ").trim())
                .contains("scope_kind = 'PLATFORM'")
                .contains("task_type = 'SYNC_DEVICE_ENTRY_URL'")
                .contains("target_type = 'DEVICE_ASSET'")
                .contains("source_device_command_id IS NULL")
                .contains("asset.hardware_sn = ?")
                .endsWith("FOR UPDATE");
    }

    private static JdbcTemplate taskJdbc(
            String state,
            String blockedReasonCode,
            String blockedDiagnostic,
            String envelope) throws Exception {
        return taskJdbc(
                state, blockedReasonCode, blockedDiagnostic, envelope, 1);
    }

    @SuppressWarnings("unchecked")
    private static JdbcTemplate taskJdbc(
            String state,
            String blockedReasonCode,
            String blockedDiagnostic,
            String envelope,
            int rowCount) throws Exception {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        ResultSet row = mock(ResultSet.class);
        when(row.getLong("id")).thenReturn(91L);
        when(row.getString("state")).thenReturn(state);
        when(row.getString("redacted_execution_snapshot"))
                .thenReturn(envelope);
        when(row.getString("blocked_reason_code"))
                .thenReturn(blockedReasonCode);
        when(row.getString("blocked_diagnostic"))
                .thenReturn(blockedDiagnostic);
        when(jdbc.query(
                anyString(), any(RowMapper.class), any(Object[].class)))
                .thenAnswer(invocation -> {
                    RowMapper<Object> mapper = invocation.getArgument(1);
                    Object mapped = mapper.mapRow(row, 0);
                    return java.util.stream.IntStream.range(0, rowCount)
                            .mapToObj(ignored -> mapped)
                            .toList();
                });
        return jdbc;
    }

    private static String frozenEnvelope(
            UUID commandUid,
            String hardwareSn,
            String urlSha256) {
        return """
                {
                  "commandUid": "%s",
                  "commandType": "SYNC_DEVICE_ENTRY_URL",
                  "targetDeviceName": "%s",
                  "target": {
                    "type": "DEVICE_ASSET",
                    "uid": "%s"
                  },
                  "payload": {
                    "deviceEntryUrlSha256": "%s"
                  }
                }
                """.formatted(
                commandUid, hardwareSn, hardwareSn, urlSha256);
    }
}
