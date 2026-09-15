package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeliveryDoorTravelWaitMigrationTest {

    @Test
    void v84AllowsTheThreeSecondObservedDoorTravelWithoutRewritingReleases()
            throws IOException {
        String sql = Files.readString(resolveMigration(), StandardCharsets.UTF_8);

        assertTrue(sql.contains("DROP CHECK ck_dev_config_door_travel_wait"));
        assertTrue(sql.contains("SET DEFAULT 3000"));
        assertTrue(sql.contains("delivery_door_travel_wait_ms BETWEEN 3000 AND 45000"));
        assertFalse(sql.toUpperCase().contains("UPDATE DEV_CONFIG_VERSION"));
    }

    private static Path resolveMigration() {
        Path relative = Path.of("src/main/resources/db/p0-migration/"
                + "V84__delivery_door_travel_wait.sql");
        Path direct = Path.of("").toAbsolutePath().resolve(relative);
        if (Files.isRegularFile(direct)) {
            return direct;
        }
        return Path.of("").toAbsolutePath().resolve("ecobin-bootstrap").resolve(relative);
    }
}
