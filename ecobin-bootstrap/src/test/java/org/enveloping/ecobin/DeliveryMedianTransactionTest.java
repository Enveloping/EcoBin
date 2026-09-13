package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.port.DeliveryCompletionBusinessWriter;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionBusinessResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.delivery.DeliveryCompletionFactsRefFactory;
import org.enveloping.ecobin.device.application.delivery.TrustedDeliveryCompletionService;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.framework.reliability.*;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.annotation.ClassPathBeanDefinitionScanner;
import org.springframework.core.type.filter.AssignableTypeFilter;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.SingleConnectionDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.DriverManager;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/** Device-side transaction seam on MySQL; business/order and reliable-task ports are probes.
 * The physical-result table retains the migrated CHECKs and unique keys; scoped lookup fixtures
 * are deliberately minimal and are NOT proof of full foreign-key or funds integration. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1as(?:\\?.*)?")
class DeliveryMedianTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private AnnotationConfigApplicationContext references;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private TrustedOrganizationInboxRefFactory inboxReferences;
    private TrustedDeliveryCompletionService service;
    private ObjectNode normalized;
    private boolean failConfirmation;
    private boolean allowQuarantine;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_MEDIAN_MYSQL_URL"),
                System.getenv().getOrDefault("ECOBIN_MEDIAN_MYSQL_USER", "root"),
                System.getenv().getOrDefault("ECOBIN_MEDIAN_MYSQL_PASSWORD", ""));
        dataSource = new SingleConnectionDataSource(connection, true);
        assertEquals("ecobin_median_p1as", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        jdbc = new JdbcTemplate(dataSource);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        references = new AnnotationConfigApplicationContext();
        var scanner = new ClassPathBeanDefinitionScanner(references, false);
        for (Class<?> factory : List.of(TrustedOrganizationInboxRefFactory.class,
                DeliveryCompletionFactsRefFactory.class, DeviceAssetTaskRefFactory.class)) {
            scanner.addIncludeFilter(new AssignableTypeFilter(factory));
        }
        scanner.scan("org.enveloping.ecobin.framework.reliability",
                "org.enveloping.ecobin.device.api.persistence");
        references.refresh();
        inboxReferences = references.getBean(TrustedOrganizationInboxRefFactory.class);
        var confirmation = new ReliableEdgeConfirmationService(mapper, jdbc,
                new DeviceConfigurationCanonicalizer(), references.getBean(DeviceAssetTaskRefFactory.class),
                new ReliableDeviceControlTaskRegistrationPort() {
                    public UUID register(ReliableDeviceControlTaskRegistration registration) {
                        jdbc.update("INSERT INTO p1as_effect VALUES ('CONFIRMATION')");
                        if (failConfirmation) throw new IllegalStateException("injected confirmation failure");
                        return UUID.randomUUID();
                    }
                    public void cancelPending(DeviceAssetTaskRef source, String type, String target, String key) {
                        throw new AssertionError("no cancellation expected");
                    }
                });
        service = new TrustedDeliveryCompletionService(jdbc, mapper,
                references.getBean(DeliveryCompletionFactsRefFactory.class), confirmation,
                new ReliableDeviceTaskProofPort() {
                    public void completeFromTrustedProof(String type, String target, String key) {
                        jdbc.update("INSERT INTO p1as_effect VALUES ('PROOF')");
                    }
                    public void completeDispatchFromTrustedCommandObservation(UUID command) {
                        throw new AssertionError("not a command observation");
                    }
                }, (source, reason, diagnostic) -> {
                    if (!allowQuarantine) throw new AssertionError(diagnostic);
                    jdbc.update("INSERT INTO p1as_effect VALUES ('QUARANTINE')");
                    return UUID.randomUUID();
                }, inboxReferences);
        prepareTables();
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/delivery-complete.event.json")));
        ObjectNode post = (ObjectNode) event.path("payload").path("finalPostCloseMeasurement");
        post.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("sampleCount", 20).put("measurementElapsedMs", 5000);
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(event.path("payload"), Map.class);
        event.put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", "SN-CONTRACT-0001");
        // Synthetic trusted-inbox identity; actual OneNet canonicalization has separate adapter tests.
        normalized.put("eventCanonicalSha256", "f".repeat(64)).set("event", event);
    }

    @AfterEach
    void close() {
        if (references != null) references.close();
        if (dataSource != null) dataSource.destroy();
    }

    @Test
    void completeMedianPreservesFactsAndDuplicateDoesNotRepeatBusinessEffect() {
        DeliveryCompletionBusinessWriter writer = reference -> reference.useOnce(facts -> {
            assertEquals("UNSTABLE", facts.physicalFact().finalPostCloseMeasurement().status());
            assertEquals("TIMEOUT_MEDIAN", facts.physicalFact().finalPostCloseMeasurement().weightValueKind());
            assertEquals(1, count("dev_device_occupancy"));
            assertEquals(1, count("dev_physical_result"));
            jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        });

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(writer));
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, complete(writer));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(1, count("dev_edge_event"));
        assertEquals(0, count("dev_device_occupancy"));
        assertEquals(3, count("p1as_effect"));
        assertEquals("BUSINESS_CONFIRMED", jdbc.queryForObject(
                "SELECT status FROM dev_delivery_session", String.class));
        assertEquals("TIMEOUT_MEDIAN", jdbc.queryForObject(
                "SELECT delivery_post_weight_value_kind FROM dev_physical_result", String.class));
    }

    @Test
    void lateCompletionAfterOfflineReleaseKeepsOriginalResultAndDeletesNoNewSlot() {
        jdbc.update("""
                UPDATE dev_delivery_session
                SET offline_occupancy_released_at = UTC_TIMESTAMP(3)
                WHERE id = 1
                """);
        assertEquals(1, jdbc.update(
                "DELETE FROM dev_device_occupancy WHERE asset_id = 1"));
        DeliveryCompletionBusinessWriter writer = reference ->
                reference.useOnce(facts -> {
                    assertEquals(1L, facts.deliverySessionId());
                    jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
                    return new DeliveryCompletionBusinessResult(
                            "DO-P1AS-LATE", List.of());
                });

        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(writer));
        assertEquals("BUSINESS_CONFIRMED", jdbc.queryForObject(
                "SELECT status FROM dev_delivery_session", String.class));
        assertNotNull(jdbc.queryForObject(
                "SELECT offline_occupancy_released_at FROM dev_delivery_session",
                java.time.LocalDateTime.class));
        assertEquals(0, count("dev_device_occupancy"));
        assertEquals(1, count("dev_physical_result"));
    }

    @Test
    void terminalWeightTimeoutPreservesEvidenceAndFinishesOriginalCommandAsFailed() {
        ObjectNode payload = (ObjectNode) normalized.path("event").path("payload");
        payload.put("completionReason", "TERMINAL_WEIGHT_FAILURE").putNull("deliveryNetWeightGrams");
        measurement("finalPostCloseMeasurement").put("status", "TIMEOUT")
                .put("weightValueAvailable", false).putNull("reportedWeightGrams")
                .put("weightValueKind", "NONE").put("measurementElapsedMs", 5000)
                .put("sampleCount", 0).put("sensorHealth", "TIMEOUT").put("faultCode", "WEIGHT_TIMEOUT");
        DeliveryCompletionBusinessWriter writer = reference -> reference.useOnce(facts -> {
            assertEquals(12000L, facts.physicalFact().firstPreOpenMeasurement().reportedWeightGrams());
            assertNull(facts.physicalFact().finalPostCloseMeasurement().reportedWeightGrams());
            assertEquals("WEIGHT_TIMEOUT", facts.physicalFact().finalPostCloseMeasurement().faultCode());
            assertNull(facts.physicalFact().deliveryNetWeightGrams());
            jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        });
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(writer));
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, complete(writer));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(3, count("p1as_effect"));
        assertEquals(0, count("dev_device_occupancy"));
        assertEquals("PHYSICAL_FAILED", jdbc.queryForObject(
                "SELECT physical_state FROM dev_device_command", String.class));
        assertNull(jdbc.queryForObject("SELECT delivery_post_weight_g FROM dev_physical_result", Long.class));
    }

    @ParameterizedTest
    @ValueSource(strings = {"firstPreOpenMeasurement", "finalPostCloseMeasurement"})
    void nativeMeasurementIdentityReachesBusinessWithoutBeingReplaced(String slot) {
        ObjectNode weight = measurement(slot);
        String hex = "45424d31" + String.format(java.util.Locale.ROOT, "%016x%08x",
                weight.path("mcuBootId").asLong(), weight.path("mcuEventSequence").asLong());
        String uid = hex.substring(0, 8) + "-" + hex.substring(8, 12) + "-" + hex.substring(12, 16)
                + "-" + hex.substring(16, 20) + "-" + hex.substring(20);
        weight.put("measurementUid", uid);
        DeliveryCompletionBusinessWriter writer = reference -> reference.useOnce(facts -> {
            var original = slot.equals("firstPreOpenMeasurement")
                    ? facts.physicalFact().firstPreOpenMeasurement() : facts.physicalFact().finalPostCloseMeasurement();
            assertEquals(UUID.fromString(uid), original.measurementUid());
            jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        });
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(writer));
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, complete(writer));
        String column = slot.equals("firstPreOpenMeasurement") ? "delivery_pre_measurement_uid" : "delivery_post_measurement_uid";
        assertEquals(uid, jdbc.queryForObject("SELECT " + column + " FROM dev_physical_result", String.class));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(3, count("p1as_effect"));
    }

    private TrustedDeviceEventApplyResult complete(DeliveryCompletionBusinessWriter writer) {
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(normalized.path("event").path("payload"), Map.class);
        ((ObjectNode) normalized.path("event")).put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        return transaction.execute(status -> service.complete(new TrustedDeviceInboxEvent(
                inboxReferences.issue(1, 1, 1), "DELIVERY_COMPLETE", 2,
                mapper.writeValueAsString(normalized)), writer));
    }

    @ParameterizedTest
    @ValueSource(strings = {"pre", "both", "legacy"})
    void originalMeanAndEitherMedianSlotReachBusinessWithTheirOriginalKinds(String slot) {
        ObjectNode before = measurement("firstPreOpenMeasurement");
        ObjectNode after = measurement("finalPostCloseMeasurement");
        if (!slot.equals("legacy")) {
            before.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                    .put("measurementElapsedMs", 5000).put("sampleCount", 5);
        }
        if (!slot.equals("both")) {
            after.put("status", "STABLE").put("weightValueKind", "STABLE_WINDOW_MEAN")
                    .put("measurementElapsedMs", 0).put("sampleCount", 1);
        }
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(reference -> reference.useOnce(facts -> {
            assertEquals(before.path("weightValueKind").asText(), facts.physicalFact().firstPreOpenMeasurement().weightValueKind());
            assertEquals(after.path("weightValueKind").asText(), facts.physicalFact().finalPostCloseMeasurement().weightValueKind());
            assertEquals(12000L, facts.physicalFact().firstPreOpenMeasurement().reportedWeightGrams());
            assertEquals(13250L, facts.physicalFact().finalPostCloseMeasurement().reportedWeightGrams());
            assertEquals(1250L, facts.physicalFact().deliveryNetWeightGrams());
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        })));
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void invalidMedianNeverReachesBusinessOrReleasesOccupancy(String slot, String field, Object value) {
        ObjectNode measurement = measurement(slot);
        measurement.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("measurementElapsedMs", 5000).put("sampleCount", 20);
        measurement.set(field, mapper.valueToTree(value));
        assertThrows(IllegalArgumentException.class, () -> complete(reference -> {
            throw new AssertionError("invalid measurement reached business");
        }));
        assertEquals(0, count("dev_physical_result"));
        assertEquals(0, count("dev_edge_event"));
        assertEquals(0, count("p1as_effect"));
        assertEquals(1, count("dev_device_occupancy"));
    }

    private static Stream<Arguments> invalidMedianFields() {
        Object[][] invalid = {
                {"measurementUid", null}, {"status", "STABLE"}, {"weightValueAvailable", false},
                {"reportedWeightGrams", null}, {"reportedWeightGrams", 2147483648L},
                {"reportedWeightGrams", -2147483649L}, {"measurementElapsedMs", 4999},
                {"measurementElapsedMs", 5001}, {"sampleCount", 4}, {"sampleCount", 33},
                {"calibrationVersion", 4294967296L}, {"sensorHealth", "UNKNOWN"},
                {"faultCode", "WEIGHT_UNSTABLE"}, {"mcuBootId", 0},
                {"mcuBootId", 9007199254740992L}, {"mcuEventSequence", 0},
                {"mcuEventSequence", 4294967296L}
        };
        return Stream.of("firstPreOpenMeasurement", "finalPostCloseMeasurement").flatMap(slot ->
                Stream.of(invalid).map(pair -> Arguments.of(slot, pair[0], pair[1])));
    }

    @ParameterizedTest
    @ValueSource(strings = {"calibration", "minimum", "maximum"})
    void medianMustMatchTheOriginalSessionConfiguration(String mismatch) {
        allowQuarantine = true;
        ObjectNode after = measurement("finalPostCloseMeasurement");
        switch (mismatch) {
            case "calibration" -> after.put("calibrationVersion", 5);
            case "minimum" -> after.put("reportedWeightGrams", -1);
            case "maximum" -> after.put("reportedWeightGrams", 350001);
            default -> throw new AssertionError(mismatch);
        }
        assertEquals(TrustedDeviceEventApplyResult.QUARANTINED, complete(reference -> {
            throw new AssertionError("wrong frozen configuration reached business");
        }));
        assertEquals(0, count("dev_physical_result"));
        assertEquals(0, count("dev_edge_event"));
        assertEquals(1, count("dev_device_occupancy"));
        assertEquals("IN_PROGRESS", jdbc.queryForObject("SELECT status FROM dev_delivery_session", String.class));
        assertEquals(2, count("p1as_effect")); // Quarantine evidence + negative confirmation, not business.
    }

    private ObjectNode measurement(String slot) {
        return (ObjectNode) normalized.path("event").path("payload").path(slot);
    }

    @ParameterizedTest
    @ValueSource(strings = {"measurementUid", "bootAndSequence"})
    void finalMedianCannotReuseTheFirstMeasurementIdentity(String identity) {
        ObjectNode before = measurement("firstPreOpenMeasurement");
        ObjectNode after = measurement("finalPostCloseMeasurement");
        if (identity.equals("measurementUid")) {
            after.set("measurementUid", before.get("measurementUid"));
        } else {
            after.set("mcuBootId", before.get("mcuBootId"));
            after.set("mcuEventSequence", before.get("mcuEventSequence"));
        }
        assertThrows(IllegalArgumentException.class, () -> complete(reference -> {
            throw new AssertionError("reused first measurement reached business");
        }));
        assertEquals(0, count("dev_physical_result"));
        assertEquals(1, count("dev_device_occupancy"));
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void failureAtBusinessOrFinalConfirmationRollsBackThenSameFactCanSucceed(boolean atConfirmation) {
        failConfirmation = atConfirmation;
        DeliveryCompletionBusinessWriter failingWriter = reference -> reference.useOnce(facts -> {
            jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
            if (!atConfirmation) throw new IllegalStateException("injected business failure");
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        });

        assertThrows(IllegalStateException.class, () -> complete(failingWriter));
        assertEquals(0, count("dev_physical_result"));
        assertEquals(0, count("dev_edge_event"));
        assertEquals(0, count("p1as_effect"));
        assertEquals(1, count("dev_device_occupancy"));
        assertEquals("IN_PROGRESS", jdbc.queryForObject("SELECT status FROM dev_delivery_session", String.class));
        assertEquals("PHYSICAL_STARTED", jdbc.queryForObject("SELECT physical_state FROM dev_device_command", String.class));
        assertEquals(0, jdbc.queryForObject("SELECT lock_version FROM dev_device_runtime_state", Integer.class));

        failConfirmation = false;
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, complete(reference -> reference.useOnce(facts -> {
            jdbc.update("INSERT INTO p1as_effect VALUES ('BUSINESS')");
            return new DeliveryCompletionBusinessResult("DO-P1AS", List.of());
        })));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(1, count("dev_edge_event"));
        assertEquals(3, count("p1as_effect"));
        assertEquals(0, count("dev_device_occupancy"));
    }

    private int count(String table) {
        return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class);
    }

    private void prepareTables() {
        jdbc.execute("CREATE TEMPORARY TABLE p1as_result_source LIKE dev_physical_result");
        jdbc.execute("CREATE TEMPORARY TABLE dev_physical_result LIKE p1as_result_source");
        jdbc.execute("CREATE TEMPORARY TABLE p1as_effect (kind VARCHAR(32)) ENGINE=InnoDB");
        jdbc.execute("CREATE TEMPORARY TABLE dev_device_asset (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,hardware_sn VARCHAR(64))");
        jdbc.execute("INSERT INTO dev_device_asset VALUES (1,1,1,'SN-CONTRACT-0001')");
        jdbc.execute("CREATE TEMPORARY TABLE dev_device_runtime_state (asset_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,last_device_event_at DATETIME(3),lock_version INT DEFAULT 0,updated_at DATETIME(3))");
        jdbc.execute("INSERT INTO dev_device_runtime_state (asset_id,tenant_id,organization_id) VALUES (1,1,1)");
        jdbc.execute("CREATE TEMPORARY TABLE dev_port_runtime_state (port_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT)");
        jdbc.execute("INSERT INTO dev_port_runtime_state VALUES (1,1,1,1)");
        jdbc.execute("CREATE TEMPORARY TABLE dev_port (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_no INT)");
        jdbc.execute("INSERT INTO dev_port VALUES (1,1,1,1,2)");
        jdbc.execute("""
                CREATE TEMPORARY TABLE dev_port_config_snapshot (
                  id BIGINT PRIMARY KEY, config_version_id BIGINT, port_id BIGINT,
                  tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT,
                  fullness_mode VARCHAR(32), configured_full_weight_g BIGINT,
                  fullness_settle_wait_ms BIGINT, fullness_confirmation_wait_ms BIGINT,
                  weight_measurement_timeout_ms BIGINT, weight_required_sample_count INT,
                  weight_minimum_g BIGINT, weight_maximum_g BIGINT, calibration_version BIGINT)
                """);
        jdbc.execute("INSERT INTO dev_port_config_snapshot VALUES (1,1,1,1,1,1,'WEIGHT_ONLY',50000,0,0,5000,5,0,350000,4)");
        jdbc.execute("""
                CREATE TEMPORARY TABLE dev_delivery_session (
                  id BIGINT PRIMARY KEY,session_uid CHAR(36),tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,
                  port_id BIGINT,organization_user_id BIGINT,device_config_version_id BIGINT,
                  device_config_version_no BIGINT,device_config_content_sha256 BINARY(32),
                  device_config_mcu_payload_sha256 BINARY(32),port_config_snapshot_id BIGINT,
                  delivery_config_version_id BIGINT,delivery_config_content_sha256 BINARY(32),
                  bag_id BIGINT,bag_uid_snapshot CHAR(36),bag_code_snapshot VARCHAR(32),status VARCHAR(32),
                  unit_price_yuan_per_kg DECIMAL(10,4),open_balance_floor_cent BIGINT,max_review_abs_weight_g BIGINT,
                  negative_weight_anomaly_threshold_g BIGINT,first_edge_accepted_at DATETIME(3),
                  first_physical_progress_at DATETIME(3),device_completed_at DATETIME(3),ended_at DATETIME(3),
                  end_reason VARCHAR(32),offline_occupancy_released_at DATETIME(3),
                  lock_version INT DEFAULT 0,updated_at DATETIME(3))
                """);
        jdbc.execute("""
                INSERT INTO dev_delivery_session (id,session_uid,tenant_id,organization_id,asset_id,
                  port_id,organization_user_id,device_config_version_id,device_config_version_no,
                  device_config_content_sha256,device_config_mcu_payload_sha256,port_config_snapshot_id,
                  delivery_config_version_id,delivery_config_content_sha256,bag_id,bag_uid_snapshot,
                  bag_code_snapshot,status,unit_price_yuan_per_kg,open_balance_floor_cent,
                  max_review_abs_weight_g,negative_weight_anomaly_threshold_g)
                VALUES (1,'30000000-0000-4000-8000-000000000001',1,1,1,1,1,1,8,
                  UNHEX(REPEAT('aa',32)),UNHEX(REPEAT('bb',32)),1,1,UNHEX(REPEAT('cc',32)),1,
                  '50000000-0000-4000-8000-000000000001','P1AS','IN_PROGRESS',0.4500,-1000,100000,100)
                """);
        jdbc.execute("CREATE TEMPORARY TABLE dev_device_occupancy (asset_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,occupancy_kind VARCHAR(32),delivery_session_id BIGINT,clean_operation_id BIGINT)");
        jdbc.execute("INSERT INTO dev_device_occupancy VALUES (1,1,1,'DELIVERY',1,NULL)");
        jdbc.execute("""
                CREATE TEMPORARY TABLE dev_device_command (
                  id BIGINT PRIMARY KEY,command_uid CHAR(36),tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,
                  command_type VARCHAR(32),delivery_session_id BIGINT,physical_state VARCHAR(32),
                  edge_accepted_at DATETIME(3),physical_started_at DATETIME(3),physical_ended_at DATETIME(3),
                  lock_version INT DEFAULT 0,updated_at DATETIME(3))
                """);
        jdbc.execute("INSERT INTO dev_device_command (id,command_uid,tenant_id,organization_id,asset_id,command_type,delivery_session_id,physical_state) VALUES (1,'30000000-0000-4000-8000-000000000003',1,1,1,'START_DELIVERY_SESSION',1,'PHYSICAL_STARTED')");
        jdbc.execute("""
                CREATE TEMPORARY TABLE dev_edge_event (
                  id BIGINT AUTO_INCREMENT PRIMARY KEY,event_uid CHAR(36) UNIQUE,tenant_id BIGINT,organization_id BIGINT,
                  asset_id BIGINT,edge_event_sequence BIGINT,event_type VARCHAR(48),delivery_class VARCHAR(32),
                  schema_version INT,target_type VARCHAR(32),target_stable_key_sha256 BINARY(32),
                  backend_received_at DATETIME(3),
                  device_occurred_at DATETIME(3),clock_quality VARCHAR(32),payload_sha256 BINARY(32),
                  canonical_sha256 BINARY(32),source_inbox_id BIGINT UNIQUE,created_at DATETIME(3),
                  UNIQUE (asset_id,edge_event_sequence))
                """);
    }
}
