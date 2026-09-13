package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.recycling.application.fullness.ApplyFullnessStateChangedService;
import org.enveloping.ecobin.device.application.fullness.TrustedFullnessStateChangeService;
import org.enveloping.ecobin.device.api.persistence.DeviceOwnedFullnessStateChangeFactsRefFactory;
import org.enveloping.ecobin.framework.reliability.*;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
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

/** Actual public device/recycling fullness receivers and confirmation service and MySQL transaction. LIKE copies
 * retain CHECKs/unique keys, not foreign keys/triggers. Parent queries are minimal fixtures;
 * reliable task boundaries use transactional probes, not real transport or funds. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_FULLNESS_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_fullness_p1aw(?:\\?.*)?")
class FullnessMedianTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private AnnotationConfigApplicationContext references;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private TrustedOrganizationInboxRefFactory inboxReferences;
    private ApplyFullnessStateChangedService service;
    private ObjectNode normalized;
    private String schema;
    private boolean failConfirmation;
    private long inboxId = 1;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_FULLNESS_MEDIAN_MYSQL_URL"), "root", "");
        dataSource = new SingleConnectionDataSource(connection, true);
        assertEquals("ecobin_fullness_p1aw", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        jdbc = new JdbcTemplate(dataSource);
        schema = "ecobin_p1aw_case_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.execute("CREATE DATABASE `" + schema + "`");
        connection.setCatalog(schema);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        references = new AnnotationConfigApplicationContext();
        var scanner = new ClassPathBeanDefinitionScanner(references, false);
        for (Class<?> factory : List.of(TrustedOrganizationInboxRefFactory.class, DeviceAssetTaskRefFactory.class, DeviceOwnedFullnessStateChangeFactsRefFactory.class)) {
            scanner.addIncludeFilter(new AssignableTypeFilter(factory));
        }
        scanner.scan("org.enveloping.ecobin.framework.reliability", "org.enveloping.ecobin.device.api.persistence");
        references.refresh();
        inboxReferences = references.getBean(TrustedOrganizationInboxRefFactory.class);
        var confirmation = new ReliableEdgeConfirmationService(mapper, jdbc,
                new DeviceConfigurationCanonicalizer(), references.getBean(DeviceAssetTaskRefFactory.class),
                new ReliableDeviceControlTaskRegistrationPort() {
                    public UUID register(ReliableDeviceControlTaskRegistration registration) {
                        jdbc.update("INSERT INTO p1aw_effect VALUES ('CONFIRMATION')");
                        if (failConfirmation) throw new IllegalStateException("injected confirmation failure");
                        return UUID.randomUUID();
                    }
                    public void cancelPending(DeviceAssetTaskRef source, String type, String target, String key) {
                        throw new AssertionError("no cancellation expected");
                    }
                });
        service = new ApplyFullnessStateChangedService(
                new TrustedFullnessStateChangeService(jdbc, mapper,
                        references.getBean(DeviceOwnedFullnessStateChangeFactsRefFactory.class), confirmation), jdbc);
        prepareTables();
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/fullness-state-changed.event.json")));
        normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", "SN-CONTRACT-0001");
        normalized.put("eventCanonicalSha256", "f".repeat(64)).set("event", event);
        measurement().put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("sampleCount", 20).put("measurementElapsedMs", 5000);
        payload().put("sourceWorkType", "CLEAN_OPERATION").put("fullnessMode", "WEIGHT_ONLY")
                .put("fullnessSensorValue", "CLEAR").put("confirmationBasis", "MCU_INDEPENDENT_RECHECK");
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertTrue(schema.matches("ecobin_p1aw_case_[0-9a-f]{32}"));
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (references != null) references.close();
            if (dataSource != null) dataSource.destroy();
        }
    }


    @Test
    void medianProjectsCurrentBagFullnessWithoutChangingItsBaselineOrWeightQuality() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("UNSTABLE", text("SELECT measurement_status FROM dev_fullness_state_fact"));
        assertEquals("TIMEOUT_MEDIAN", text("SELECT weight_value_kind FROM dev_fullness_state_fact"));
        assertEquals(51200L, number("SELECT total_weight_g FROM dev_fullness_state_fact"));
        assertEquals("FULL", text("SELECT confirmed_fullness_state FROM rec_port_capacity_state"));
        assertEquals(50000L, number("SELECT raw_net_weight_g FROM rec_port_capacity_state"));
        assertEquals(1200L, number("SELECT current_baseline_weight_g FROM rec_port_capacity_state"));
        assertEquals("APPLIED", text("SELECT disposition FROM rec_fullness_state_change"));
        assertEquals(1, count("p1aw_effect"));
    }


    @ParameterizedTest
    @MethodSource("invalidMeasurements")
    void invalidMedianCannotLeaveFactsCapacityOrConfirmation(String field, Object value) {
        measurement().set(field, mapper.valueToTree(value));
        assertThrows(IllegalArgumentException.class, this::apply);
        assertNoEffects();
    }

    static Stream<Arguments> invalidMeasurements() {
        return Stream.of(
                Arguments.of("status", "STABLE"), Arguments.of("status", "TIMEOUT"),
                Arguments.of("weightValueAvailable", false), Arguments.of("weightValueAvailable", null),
                Arguments.of("weightValueKind", "STABLE_WINDOW_MEAN"), Arguments.of("weightValueKind", null),
                Arguments.of("reportedWeightGrams", null), Arguments.of("reportedWeightGrams", 2147483648L),
                Arguments.of("reportedWeightGrams", -2147483649L),
                Arguments.of("measurementElapsedMs", 4999), Arguments.of("measurementElapsedMs", 5001),
                Arguments.of("sampleCount", 4), Arguments.of("sampleCount", 33),
                Arguments.of("sensorHealth", "TIMEOUT"), Arguments.of("sensorHealth", null),
                Arguments.of("faultCode", "WEIGHT_UNSTABLE"), Arguments.of("faultCode", ""),
                Arguments.of("calibrationVersion", -1), Arguments.of("calibrationVersion", 4294967296L),
                Arguments.of("mcuBootId", 0), Arguments.of("mcuBootId", 9007199254740992L),
                Arguments.of("mcuEventSequence", 0), Arguments.of("mcuEventSequence", 4294967296L),
                Arguments.of("measurementUid", "not-a-uuid"));
    }

    @Test
    void absentFaultFieldIsNotAnExplicitHealthyMedian() {
        measurement().remove("faultCode");
        assertThrows(IllegalArgumentException.class, this::apply);
        assertNoEffects();
    }

    @Test
    void duplicateDoesNotProjectOrConfirmAgain() {
        apply();
        var capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, apply());
        assertRowEquals(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
        assertEquals(1, count("dev_fullness_state_fact"));
        assertEquals(1, count("rec_fullness_state_change"));
        assertEquals(1, count("p1aw_effect"));
    }

    @Test
    void confirmationFailureRollsBackDeviceBusinessRuntimeAndCanRetry() {
        var runtime = jdbc.queryForMap("SELECT * FROM dev_device_runtime_state");
        failConfirmation = true;
        assertThrows(IllegalStateException.class, this::apply);
        assertNoEffects();
        assertRowEquals(runtime, jdbc.queryForMap("SELECT * FROM dev_device_runtime_state"));
        failConfirmation = false;
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
    }

    @Test
    void stableMeanRemainsSupportedWithItsOwnMetadata() {
        measurement().put("status", "STABLE").put("weightValueKind", "STABLE_WINDOW_MEAN")
                .put("sampleCount", 12).put("measurementElapsedMs", 1200);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("STABLE_WINDOW_MEAN", text("SELECT weight_value_kind FROM dev_fullness_state_fact"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"CLEAN_OPERATION", "DELIVERY_SESSION"})
    void eitherCommittedSourceBusinessCanReportCurrentBag(String source) {
        payload().put("sourceWorkType", source);
        jdbc.update("INSERT INTO dev_delivery_session VALUES(1,1,1,1,1,'30000000-0000-4000-8000-000000000001')");
        jdbc.update("INSERT INTO rec_delivery_order VALUES(1,1,1,1,1,1,1)");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(source, text("SELECT source_work_type FROM rec_fullness_state_change"));
        assertEquals("FULL", text("SELECT confirmed_fullness_state FROM rec_port_capacity_state"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"CLEAN_OPERATION", "DELIVERY_SESSION"})
    void sourceNotCommittedYetLeavesNoEffectsAndCanRetry(String source) {
        payload().put("sourceWorkType", source);
        jdbc.update("UPDATE rec_clean_operation SET status='IN_PROGRESS'");
        jdbc.update("INSERT INTO dev_delivery_session VALUES(1,1,1,1,1,'30000000-0000-4000-8000-000000000001')");
        assertThrows(IllegalStateException.class, this::apply);
        assertNoEffects();
        jdbc.update("UPDATE rec_clean_operation SET status='COMPLETED'");
        jdbc.update("INSERT INTO rec_delivery_order VALUES(1,1,1,1,1,1,1)");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
    }

    @ParameterizedTest
    @ValueSource(strings = {"STALE_BAG", "STALE_SEQUENCE", "NO_STATE_CHANGE"})
    void lateEvidenceCannotOverwriteNewerCurrentBagState(String disposition) {
        apply();
        var capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        newEvent(disposition.equals("NO_STATE_CHANGE") ? 1046 : 1044);
        if (disposition.equals("STALE_BAG")) {
            jdbc.update("INSERT INTO rec_bag VALUES(2,1,1,'30000000-0000-4000-8000-000000000003')");
            jdbc.update("UPDATE rec_clean_operation SET new_bag_id=2");
            payload().put("bagUid", "30000000-0000-4000-8000-000000000003");
        }
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(disposition, text("SELECT disposition FROM rec_fullness_state_change ORDER BY id DESC LIMIT 1"));
        assertEquals(2, count("dev_fullness_state_fact"));
        assertEquals(2, count("p1aw_effect"));
        if (!disposition.equals("NO_STATE_CHANGE")) {
            assertRowEquals(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
        } else {
            assertEquals(1046L, number("SELECT last_fullness_edge_event_sequence FROM rec_port_capacity_state"));
        }
    }

    @ParameterizedTest
    @ValueSource(strings = {"fullnessMode", "configuredFullWeightGrams", "state", "weightFull", "fullnessPercentHundredths"})
    void contradictoryConfigurationOrDecisionCannotChangeState(String field) {
        switch (field) {
            case "fullnessMode" -> payload().put(field, "SENSOR_ONLY");
            case "configuredFullWeightGrams" -> payload().put(field, 50001);
            case "state" -> payload().put(field, "NOT_FULL");
            case "weightFull" -> payload().put(field, false);
            case "fullnessPercentHundredths" -> payload().put(field, 9999);
        }
        assertThrows(RuntimeException.class, this::apply);
        assertNoEffects();
    }

    @ParameterizedTest
    @ValueSource(longs = {0, 1200, -2147483648L, 2147483647L})
    void preservesRealValueAndCalculatesFullnessWithoutCalibratingHardware(long grams) {
        measurement().put("reportedWeightGrams", grams);
        long net = Math.max(0, grams - 1200);
        payload().put("weightFull", net >= 50000).put("fullnessPercentHundredths", net * 10000 / 50000)
                .put("state", net >= 50000 ? "FULL" : "NOT_FULL");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(grams, number("SELECT total_weight_g FROM dev_fullness_state_fact"));
        assertEquals(grams - 1200, number("SELECT raw_net_weight_g FROM rec_port_capacity_state"));
        assertEquals(1200L, number("SELECT current_baseline_weight_g FROM rec_port_capacity_state"));
    }

    private void newEvent(long sequence) {
        inboxId++;
        ObjectNode event = (ObjectNode) normalized.path("event");
        String stateUid = UUID.randomUUID().toString();
        event.put("eventUid", UUID.randomUUID().toString()).put("edgeEventSequence", sequence);
        ((ObjectNode) event.path("target")).put("uid", stateUid);
        payload().put("stateChangeUid", stateUid);
        measurement().put("measurementUid", UUID.randomUUID().toString());
        normalized.put("eventCanonicalSha256", "e".repeat(64));
    }

    @Test
    void newerNotFullFactClearsOnlyFullnessAndRetainsExistingBaseline() {
        apply();
        newEvent(1046);
        measurement().put("reportedWeightGrams", 1200);
        payload().put("state", "NOT_FULL").put("weightFull", false).put("fullnessPercentHundredths", 0);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("NOT_FULL", text("SELECT confirmed_fullness_state FROM rec_port_capacity_state"));
        assertEquals("VALID", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertEquals(1200L, number("SELECT current_baseline_weight_g FROM rec_port_capacity_state"));
        assertEquals(0L, number("SELECT raw_net_weight_g FROM rec_port_capacity_state"));
        assertEquals(2, count("p1aw_effect"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"SENSOR_ONLY", "SENSOR_OR_WEIGHT"})
    void infraredCanStillDetermineFullnessWithoutInventingMissingBaseline(String mode) {
        payload().put("fullnessMode", mode).put("fullnessSensorValue", "BLOCKED")
                .putNull("baselineWeightGrams").putNull("weightFull").putNull("fullnessPercentHundredths");
        jdbc.update("UPDATE dev_port_config_snapshot SET fullness_mode=?",
                mode.equals("SENSOR_ONLY") ? "INFRARED_ONLY" : "INFRARED_OR_WEIGHT");
        jdbc.update("DELETE FROM rec_port_weight_baseline");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("FULL", text("SELECT confirmed_fullness_state FROM rec_port_capacity_state"));
        assertEquals("UNINITIALIZED", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertNull(number("SELECT raw_net_weight_g FROM rec_port_capacity_state"));
        assertNull(number("SELECT current_baseline_weight_g FROM rec_port_capacity_state"));
        assertEquals(51200L, number("SELECT total_weight_g FROM dev_fullness_state_fact"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"version", "contentSha256", "mcuPayloadSha256", "calibrationVersion"})
    void frozenConfigurationIdentityCannotBeReplacedByAnotherSnapshot(String field) {
        ObjectNode frozen = (ObjectNode) payload().path("frozenConfig");
        if (field.equals("version")) frozen.put(field, 9);
        else if (field.equals("calibrationVersion")) measurement().put(field, 5);
        else frozen.put(field, "c".repeat(64));
        assertThrows(RuntimeException.class, this::apply);
        assertNoEffects();
    }

    private void assertNoEffects() {
        for (String table : List.of("dev_edge_event", "dev_fullness_state_fact",
                "rec_fullness_state_change", "rec_port_capacity_state", "p1aw_effect")) assertEquals(0, count(table), table);
    }

    private static void assertRowEquals(Map<String, Object> expected, Map<String, Object> actual) {
        assertEquals(expected.keySet(), actual.keySet());
        expected.forEach((column, value) -> {
            if (value instanceof byte[] bytes) assertArrayEquals(bytes, (byte[]) actual.get(column), column);
            else assertEquals(value, actual.get(column), column);
        });
    }

    private TrustedDeviceEventApplyResult apply() {
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(payload(), Map.class);
        ((ObjectNode) normalized.path("event")).put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        return transaction.execute(status -> service.apply(new TrustedDeviceInboxEvent(
                inboxReferences.issue(inboxId, 1, 1), "FULLNESS_STATE_CHANGED", 2, mapper.writeValueAsString(normalized))));
    }

    private ObjectNode payload() { return (ObjectNode) normalized.path("event").path("payload"); }
    private ObjectNode measurement() { return (ObjectNode) payload().path("totalWeightMeasurement"); }
    private int count(String table) { return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class); }
    private String text(String sql) { return jdbc.queryForObject(sql, String.class); }
    private Long number(String sql) { return jdbc.queryForObject(sql, Long.class); }

    private void prepareTables() {
        for (String table : List.of("dev_fullness_state_fact", "dev_edge_event",
                "rec_fullness_state_change", "rec_port_capacity_state", "dev_device_runtime_state")) {
            jdbc.execute("CREATE TABLE " + table + " LIKE ecobin_fullness_p1aw." + table);
        }
        jdbc.execute("CREATE TABLE p1aw_effect(kind VARCHAR(32)) ENGINE=InnoDB");
        jdbc.execute("CREATE TABLE dev_device_asset(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,hardware_sn VARCHAR(64),expected_port_count INT)");
        jdbc.execute("CREATE TABLE dev_port(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_no INT)");
        jdbc.execute("CREATE TABLE dev_port_runtime_state(port_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT)");
        jdbc.execute("CREATE TABLE rec_bag(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,bag_uid VARCHAR(36))");
        jdbc.execute("CREATE TABLE rec_bag_current_occupancy(bag_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,port_id BIGINT,occupancy_type VARCHAR(32))");
        jdbc.execute("CREATE TABLE rec_port_weight_baseline(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,port_id BIGINT,bag_id BIGINT,version_no BIGINT,baseline_weight_g BIGINT)");
        jdbc.execute("CREATE TABLE rec_clean_operation(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_id BIGINT,operation_uid VARCHAR(36),status VARCHAR(32),completion_record_id BIGINT,new_bag_id BIGINT)");
        jdbc.execute("CREATE TABLE dev_delivery_session(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_id BIGINT,session_uid VARCHAR(36))");
        jdbc.execute("CREATE TABLE rec_delivery_order(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_id BIGINT,delivery_session_id BIGINT,bag_id BIGINT)");
        jdbc.execute("CREATE TABLE dev_config_version(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,version_no BIGINT,content_sha256 BINARY(32),mcu_payload_sha256 BINARY(32))");
        jdbc.execute("CREATE TABLE dev_port_config_snapshot(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,config_version_id BIGINT,port_id BIGINT,calibration_version BIGINT,fullness_mode VARCHAR(32),configured_full_weight_g BIGINT)");
        jdbc.update("INSERT INTO dev_device_asset VALUES(1,1,1,'SN-CONTRACT-0001',2)");
        jdbc.update("INSERT INTO dev_port VALUES(1,1,1,1,2)");
        jdbc.update("INSERT INTO dev_port_runtime_state VALUES(1,1,1,1)");
        jdbc.update("INSERT INTO rec_bag VALUES(1,1,1,'30000000-0000-4000-8000-000000000002')");
        jdbc.update("INSERT INTO rec_bag_current_occupancy VALUES(1,1,1,1,'PORT_BOUND')");
        jdbc.update("INSERT INTO rec_port_weight_baseline VALUES(1,1,1,1,1,1,1200)");
        jdbc.update("INSERT INTO rec_clean_operation VALUES(1,1,1,1,1,'30000000-0000-4000-8000-000000000001','COMPLETED',1,1)");
        jdbc.update("INSERT INTO dev_config_version VALUES(1,1,1,1,8,UNHEX(REPEAT('a',64)),UNHEX(REPEAT('b',64)))");
        jdbc.update("INSERT INTO dev_port_config_snapshot VALUES(1,1,1,1,1,1,4,'WEIGHT_ONLY',50000)");
        jdbc.update("""
                INSERT INTO dev_device_runtime_state(asset_id,tenant_id,organization_id,edge_connection_status,
                    mcu_link_status,safety_status,aggregate_weight_health,camera_health,local_storage_health,
                    clock_sync_health,applied_config_version_no,applied_config_content_sha256,applied_mcu_payload_sha256,created_at,updated_at)
                VALUES(1,1,1,'UNKNOWN','UNKNOWN','SAFE','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN',8,
                    UNHEX(REPEAT('a',64)),UNHEX(REPEAT('b',64)),NOW(3),NOW(3))
                """);
    }
}
