package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceSoftwareCompatibilityReceivePlaneMigrationTest {

    private static final Path V63 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V63__device_software_compatibility_receive_plane.sql");

    @Test
    void createsReceiveOnlyFactsAndKeepsExistingDevicesOnLegacyRules()
            throws IOException {
        String sql = Files.readString(resolve(V63), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql)
                .contains(
                        "CREATE TABLE dev_edge_software_release",
                        "CREATE TABLE dev_device_management_profile",
                        "CREATE TABLE dev_device_software_fact",
                        "CREATE TABLE dev_device_compatibility_projection",
                        "SELECT asset.id, 'LEGACY_DIRECT'",
                        "trg_dev_asset_v63_management_defaults",
                        "trg_dev_management_profile_v63_no_downgrade",
                        "trg_dev_compatibility_projection_v63_no_downgrade",
                        "trg_dev_edge_software_release_v63_immutable",
                        "CREATE DEFINER = 'ecobin_trigger_definer'@'%'",
                        "negotiated_communication_business_major "
                                + "SMALLINT UNSIGNED NULL",
                        "CHAR_LENGTH(version_name) BETWEEN 1 AND 32",
                        "required_fixed_frame_revision BETWEEN 1 AND 255",
                        "mcu_fixed_frame_revision BETWEEN 1 AND 255",
                        "active_business_release_uid IS NOT NULL",
                        "mcu_firmware_version IS NOT NULL",
                        "source_event_uid IS NOT NULL",
                        "primary_reason_code IS NOT NULL",
                        "business_admission_status IN "
                                + "('ACCEPTING', 'PAUSED', 'UNKNOWN')")
                .doesNotContain(
                        "CREATE TABLE dev_edge_software_deployment",
                        "CREATE TABLE dev_edge_software_rollout",
                        "CREATE TABLE dev_edge_software_command",
                        "download_url",
                        "presigned_url");
    }

    @Test
    void nullableTuplesCannotPassChecksThroughSqlUnknown()
            throws IOException {
        String sql = Files.readString(resolve(V63), StandardCharsets.UTF_8)
                .replace("\r\n", "\n");

        assertThat(sql).contains(
                "transition_source_event_uid IS NOT NULL",
                "negotiated_communication_business_major IS NOT NULL",
                "negotiated_communication_business_minor IS NOT NULL",
                "negotiated_communication_updater_major IS NOT NULL",
                "negotiated_communication_updater_minor IS NOT NULL",
                "negotiated_updater_business_major IS NOT NULL",
                "negotiated_updater_business_minor IS NOT NULL",
                "active_business_release_sequence IS NOT NULL",
                "active_business_version_name IS NOT NULL",
                "active_business_package_sha256 IS NOT NULL",
                "mcu_firmware_version_code IS NOT NULL",
                "mcu_firmware_identity_hex IS NOT NULL",
                "mcu_fixed_frame_revision IS NOT NULL",
                "management_state_sequence IS NOT NULL",
                "received_at IS NOT NULL",
                "primary_reason_message IS NOT NULL");
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
