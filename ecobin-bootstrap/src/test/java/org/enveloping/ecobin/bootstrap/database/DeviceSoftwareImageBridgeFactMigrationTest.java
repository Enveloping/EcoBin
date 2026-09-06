package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DeviceSoftwareImageBridgeFactMigrationTest {

    @Test
    void v68AllowsOnlyMatchingFactoryImageComponentsWithoutMutatingFacts()
            throws IOException {
        String migration = Files.readString(
                moduleSource("src/main/resources/db/p0-migration/"
                        + "V68__device_software_image_bridge_fact.sql"),
                StandardCharsets.UTF_8);
        String normalized = migration.toLowerCase(Locale.ROOT);

        assertTrue(normalized.contains(
                "drop check ck_dev_software_fact_process"));
        assertTrue(normalized.contains(
                "add constraint ck_dev_software_fact_process check"));
        assertTrue(normalized.contains(
                "^communication-[0-9]{8}-[0-9]{2,6}$"));
        assertTrue(normalized.contains(
                "^updater-[0-9]{8}-[0-9]{2,6}$"));
        assertTrue(normalized.contains(
                "substring(\n                            communication_agent_version, 15"));
        assertTrue(normalized.contains(
                "substring(device_updater_version, 9)"));
        assertTrue(normalized.contains(
                "negotiated_communication_business_major is not null"));
        assertTrue(normalized.contains(
                "negotiated_updater_business_major is not null"));
        assertFalse(normalized.matches("(?s).*\\bupdate\\s+dev_device_software_fact\\b.*"));
        assertFalse(normalized.matches("(?s).*\\bdelete\\s+from\\s+dev_device_software_fact\\b.*"));
    }

    private static Path moduleSource(String relativePath) {
        Path workingDirectory = Path.of("").toAbsolutePath();
        Path direct = workingDirectory.resolve(relativePath);
        if (Files.isRegularFile(direct)) {
            return direct;
        }
        Path nested = workingDirectory.resolve("ecobin-bootstrap")
                .resolve(relativePath);
        assertTrue(
                Files.isRegularFile(nested),
                () -> "missing source file " + nested);
        return nested;
    }
}
