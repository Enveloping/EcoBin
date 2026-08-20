package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import tools.jackson.databind.node.ObjectNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class TrustedMcuFirmwareIdentityServiceTest {

    private JdbcTemplate jdbc;
    private TrustedMcuFirmwareIdentityService service;
    private JsonMapper objectMapper;

    @BeforeEach
    void setUp() {
        DriverManagerDataSource dataSource = new DriverManagerDataSource();
        dataSource.setDriverClassName("org.h2.Driver");
        dataSource.setUrl("jdbc:h2:mem:mcu_identity_"
                + UUID.randomUUID()
                + ";MODE=MySQL;DB_CLOSE_DELAY=-1");
        jdbc = new JdbcTemplate(dataSource);
        jdbc.execute("""
                CREATE TABLE dev_device_asset (
                    id BIGINT PRIMARY KEY,
                    mcu_firmware_version_code BIGINT,
                    mcu_firmware_identity_hex VARCHAR(16),
                    mcu_fixed_frame_revision INT,
                    updated_at TIMESTAMP NOT NULL
                )
                """);
        jdbc.update("""
                        INSERT INTO dev_device_asset (id, updated_at)
                        VALUES (1, ?)
                        """,
                LocalDateTime.now(ZoneOffset.UTC));
        service = new TrustedMcuFirmwareIdentityService(jdbc);
        objectMapper = JsonMapper.builder().build();
    }

    @Test
    void internalErrorStatusCannotRegisterRevisionTwo() {
        ObjectNode payload = validPayload();
        payload.withObject("mcuFirmwareIdentity").put("statusCode", 3);

        assertThrows(
                UntrustedInboxSourceException.class,
                () -> service.applyTrustedRuntimeObservation(
                        1,
                        payload,
                        LocalDateTime.now(ZoneOffset.UTC)));

        assertNull(jdbc.queryForObject(
                "SELECT mcu_fixed_frame_revision"
                        + " FROM dev_device_asset WHERE id = 1",
                Integer.class));
    }

    @Test
    void successfulF3RegistersOneCompleteIdentity() {
        service.applyTrustedRuntimeObservation(
                1,
                validPayload(),
                LocalDateTime.now(ZoneOffset.UTC));

        assertEquals(2, jdbc.queryForObject(
                "SELECT mcu_fixed_frame_revision"
                        + " FROM dev_device_asset WHERE id = 1",
                Integer.class));
        assertEquals(10_900L, jdbc.queryForObject(
                "SELECT mcu_firmware_version_code"
                        + " FROM dev_device_asset WHERE id = 1",
                Long.class));
        assertEquals("fedcba9876543210", jdbc.queryForObject(
                "SELECT mcu_firmware_identity_hex"
                        + " FROM dev_device_asset WHERE id = 1",
                String.class));
    }

    private ObjectNode validPayload() {
        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("mcuFirmwareVersion", "1.9.0");
        payload.putObject("mcuFirmwareIdentity")
                .put("queryStatus", "OK")
                .put("statusCode", 0)
                .put("fixedFrameRevision", 2)
                .put("firmwareVersionCode", 10_900L)
                .put("firmwareVersion", "1.9.0")
                .put("firmwareIdentityHex", "fedcba9876543210");
        return payload;
    }
}
