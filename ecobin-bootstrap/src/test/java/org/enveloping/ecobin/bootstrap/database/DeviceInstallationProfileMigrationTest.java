package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertTrue;

class DeviceInstallationProfileMigrationTest {

    private static final Path V48 = Path.of(
            "src/main/resources/db/p0-migration/"
                    + "V48__device_installation_profiles.sql");

    @Test
    void movesCurrentInstallationFactsToPermanentAssetWithoutDroppingHistory()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8);

        assertTrue(sql.contains("ADD COLUMN installation_display_name"));
        assertTrue(sql.contains("ROW_NUMBER() OVER"));
        assertTrue(sql.contains("ORDER BY config.version_no DESC"));
        assertTrue(sql.contains(
                "CONVERT(asset.hardware_sn USING utf8mb4)"));
        assertTrue(sql.contains("installation_profile_version"));
        assertTrue(sql.contains("fk_dev_asset_installation_updater_v48"));
        assertTrue(sql.contains(
                "MODIFY COLUMN device_display_name VARCHAR(100) NULL"));
        assertTrue(sql.contains(
                "ck_dev_config_no_installation_fields_v48"));
    }

    private static Path resolve() {
        if (Files.isRegularFile(V48)) {
            return V48;
        }
        Path nested = Path.of("ecobin-bootstrap").resolve(V48);
        assertTrue(Files.isRegularFile(nested));
        return nested;
    }
}
