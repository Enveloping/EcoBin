package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class DeliveryRecoveryQuarantineMigrationTest {

    private static final Path V69 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V69__delivery_recovery_quarantine.sql");

    @Test
    void createsIssueOnlyRecoveryLedgerOutsideOrdersAndFunds()
            throws IOException {
        String sql = Files.readString(resolve(V69), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");
        String normalized = sql.toLowerCase(Locale.ROOT);

        assertThat(sql).contains(
                "DROP CHECK ck_ops_task_sources_v66",
                "ADD CONSTRAINT ck_ops_task_sources_v69",
                "'QUARANTINE_DELIVERY_RECOVERY'",
                "CREATE TABLE dev_delivery_recovery_quarantine",
                "UNIQUE (active_delivery_session_id)",
                "physical_outcome_unknown_confirmed = 1",
                "device_power_cycled_confirmed = 1",
                "motion_area_clear_confirmed = 1",
                "delivery_door_closed_confirmed = 1",
                "mechanism_clear_confirmed = 1",
                "business_value = 'NONE'",
                "terminal_event_uid = recovery_uid",
                "state IN ('QUEUED', 'APPLIED', 'CANCELLED')",
                "existing_data_json JSON NULL",
                "terminal_payload_json JSON NULL");
        assertThat(normalized).doesNotContain(
                "insert into rec_",
                "update rec_",
                "insert into fin_",
                "update fin_",
                "withdrawal",
                "auto_review");
    }

    private static Path resolve(Path relative) {
        Path direct = relative.toAbsolutePath().normalize();
        if (Files.exists(direct)) {
            return direct;
        }
        return Path.of("ecobin-bootstrap")
                .resolve(relative)
                .toAbsolutePath()
                .normalize();
    }
}
