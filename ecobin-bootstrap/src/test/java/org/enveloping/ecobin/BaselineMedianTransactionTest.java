package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.device.application.target.TrustedOrangePiRuntimeFactService;
import org.enveloping.ecobin.device.application.target.TrustedMcuFirmwareIdentityService;
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

/** Actual public baseline receiver/confirmation service and MySQL transaction. LIKE copies
 * retain CHECKs/unique keys, not foreign keys/triggers. Parent queries are minimal fixtures;
 * reliable task boundaries use transactional probes, not real transport or funds. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_BASELINE_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_baseline_p1av(?:\\?.*)?")
class BaselineMedianTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private AnnotationConfigApplicationContext references;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private TrustedOrganizationInboxRefFactory inboxReferences;
    private TrustedOrangePiRuntimeFactService service;
    private ObjectNode normalized;
    private String schema;
    private boolean failConfirmation;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_BASELINE_MEDIAN_MYSQL_URL"), "root", "");
        dataSource = new SingleConnectionDataSource(connection, true);
        assertEquals("ecobin_baseline_p1av", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        jdbc = new JdbcTemplate(dataSource);
        schema = "ecobin_p1av_case_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.execute("CREATE DATABASE `" + schema + "`");
        connection.setCatalog(schema);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        references = new AnnotationConfigApplicationContext();
        var scanner = new ClassPathBeanDefinitionScanner(references, false);
        for (Class<?> factory : List.of(TrustedOrganizationInboxRefFactory.class, DeviceAssetTaskRefFactory.class)) {
            scanner.addIncludeFilter(new AssignableTypeFilter(factory));
        }
        scanner.scan("org.enveloping.ecobin.framework.reliability", "org.enveloping.ecobin.device.api.persistence");
        references.refresh();
        inboxReferences = references.getBean(TrustedOrganizationInboxRefFactory.class);
        var confirmation = new ReliableEdgeConfirmationService(mapper, jdbc,
                new DeviceConfigurationCanonicalizer(), references.getBean(DeviceAssetTaskRefFactory.class),
                new ReliableDeviceControlTaskRegistrationPort() {
                    public UUID register(ReliableDeviceControlTaskRegistration registration) {
                        jdbc.update("INSERT INTO p1av_effect VALUES ('CONFIRMATION')");
                        if (failConfirmation) throw new IllegalStateException("injected confirmation failure");
                        return UUID.randomUUID();
                    }
                    public void cancelPending(DeviceAssetTaskRef source, String type, String target, String key) {
                        throw new AssertionError("no cancellation expected");
                    }
                });
        service = new TrustedOrangePiRuntimeFactService(jdbc, mapper, confirmation,
                new ReliableDeviceTaskProofPort() {
                    public void completeFromTrustedProof(String type, String target, String key) {
                        throw new AssertionError("no generic task completion expected");
                    }
                    public void completeDispatchFromTrustedCommandObservation(UUID command) {
                        jdbc.update("INSERT INTO p1av_effect VALUES ('PROOF')");
                    }
                }, (source, reason, diagnostic) -> { throw new AssertionError(diagnostic); }, inboxReferences,
                null, null, null, null, new TrustedMcuFirmwareIdentityService(jdbc), List.of(), List.of());
        prepareTables();
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/baseline-measurement-complete.event.json")));
        normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", "SN-CONTRACT-0001");
        normalized.put("eventCanonicalSha256", "f".repeat(64)).set("event", event);
        measurement().put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("sampleCount", 20).put("measurementElapsedMs", 5000);
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertTrue(schema.matches("ecobin_p1av_case_[0-9a-f]{32}"));
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (references != null) references.close();
            if (dataSource != null) dataSource.destroy();
        }
    }

    @Test
    void usableMedianEstablishesCurrentBagBaselineWithoutRewritingQuality() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("COMPLETED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals("UNSTABLE", text("SELECT baseline_measurement_status FROM dev_physical_result"));
        assertEquals("TIMEOUT_MEDIAN", text("SELECT baseline_weight_value_kind FROM dev_physical_result"));
        assertEquals(1180L, number("SELECT baseline_total_weight_g FROM dev_physical_result"));
        assertNull(number("SELECT baseline_last_observed_weight_g FROM dev_physical_result"));
        assertNull(text("SELECT baseline_fault_code FROM dev_physical_result"));
        assertEquals(1180L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
        assertEquals("AUTOMATIC_INITIAL", text("SELECT source_type FROM rec_port_weight_baseline"));
        assertEquals("VALID", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertEquals("READY", text("SELECT detection_gate FROM rec_port_capacity_state"));
        assertEquals(0L, number("SELECT raw_net_weight_g FROM rec_port_capacity_state"));
        assertEquals("READY", text("SELECT tare_status FROM dev_factory_installed_bag"));
        assertEquals("PHYSICAL_SUCCEEDED", text("SELECT physical_state FROM dev_device_command"));
        assertEquals(2, count("p1av_effect"));
    }

    @Test
    void supersededCapacityGenerationKeepsEvidenceWithoutChangingCurrentTareOrCapacity() {
        jdbc.update("UPDATE rec_port_capacity_state SET lock_version=8");
        Map<String, Object> capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        Map<String, Object> tare = jdbc.queryForMap("SELECT * FROM dev_factory_installed_bag");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("STALE_IGNORED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals("BASELINE_FACT_STALE", text("SELECT fault_code FROM rec_port_baseline_measurement"));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=DATABASE()", Integer.class));
        assertAll(
                () -> assertEquals(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state")),
                () -> assertEquals(tare, jdbc.queryForMap("SELECT * FROM dev_factory_installed_bag")));
    }

    @ParameterizedTest
    @MethodSource("invalidMedian")
    void rejectsMalformedMedianBeforeAnyFactOrBaselineIsCommitted(String field, Object value) {
        measurement().set(field, mapper.valueToTree(value));
        RuntimeException error = assertThrows(RuntimeException.class, this::apply);
        assertTrue(error instanceof IllegalArgumentException || error instanceof UntrustedInboxSourceException,
                () -> "expected semantic rejection, got " + error.getClass().getName());
        assertInitialState();
    }

    static Stream<Arguments> invalidMedian() {
        return Stream.of(
                Arguments.of("status", "STABLE"), Arguments.of("status", null),
                Arguments.of("weightValueAvailable", false), Arguments.of("weightValueAvailable", null),
                Arguments.of("reportedWeightGrams", null), Arguments.of("reportedWeightGrams", 2147483648L),
                Arguments.of("reportedWeightGrams", -2147483649L), Arguments.of("reportedWeightGrams", 0.5),
                Arguments.of("measurementElapsedMs", 4999), Arguments.of("measurementElapsedMs", 5001),
                Arguments.of("measurementElapsedMs", null), Arguments.of("sampleCount", 4),
                Arguments.of("sampleCount", 33), Arguments.of("sampleCount", null),
                Arguments.of("sensorHealth", "TIMEOUT"), Arguments.of("sensorHealth", null),
                Arguments.of("faultCode", "WEIGHT_UNSTABLE"), Arguments.of("faultCode", ""),
                Arguments.of("calibrationVersion", -1), Arguments.of("calibrationVersion", 4294967296L),
                Arguments.of("calibrationVersion", null), Arguments.of("mcuBootId", 0),
                Arguments.of("mcuBootId", 9007199254740992L), Arguments.of("mcuBootId", null),
                Arguments.of("mcuEventSequence", 0), Arguments.of("mcuEventSequence", 4294967296L),
                Arguments.of("mcuEventSequence", null), Arguments.of("measurementUid", null),
                Arguments.of("measurementUid", "not-a-uuid"),
                Arguments.of("measurementUid", "86000000-0000-1000-8000-000000000002"));
    }

    @Test
    void omittedFaultEvidenceIsNotEquivalentToExplicitNoFault() {
        measurement().remove("faultCode");
        assertThrows(UntrustedInboxSourceException.class, this::apply);
        assertInitialState();
    }

    @Test
    void duplicateDoesNotCreateAnotherBaselineOrRepeatConfirmation() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        Map<String, Object> capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, apply());
        assertEquals(1, count("dev_edge_event"));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(1, count("rec_port_weight_baseline"));
        assertEquals(2, count("p1av_effect"));
        assertSameDatabaseRow(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
    }

    @Test
    void confirmationFailureRollsBackBaselineCapacityTareAndCommandThenRetries() {
        failConfirmation = true;
        assertThrows(IllegalStateException.class, this::apply);
        assertInitialState();
        failConfirmation = false;
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(1180L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
        assertEquals("READY", text("SELECT tare_status FROM dev_factory_installed_bag"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"STABLE_WINDOW_MEAN", "LAST_OBSERVED"})
    void legacySingleSampleStableMeasurementKeepsItsKind(String kind) {
        measurement().put("status", "STABLE").put("weightValueKind", kind)
                .put("sampleCount", 1).put("measurementElapsedMs", 0);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("COMPLETED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals(kind, text("SELECT baseline_weight_value_kind FROM dev_physical_result"));
        assertEquals(1180L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
    }

    @Test
    void negativeMedianIsEvidenceButCannotBecomeAnEmptyBagBaseline() {
        measurement().put("reportedWeightGrams", -1);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("FAILED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals("NEGATIVE_EMPTY_BAG_WEIGHT", text("SELECT fault_code FROM rec_port_baseline_measurement"));
        assertEquals(-1L, number("SELECT baseline_total_weight_g FROM dev_physical_result"));
        assertNull(text("SELECT baseline_fault_code FROM dev_physical_result"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertEquals("FAILED", text("SELECT tare_status FROM dev_factory_installed_bag"));
    }

    @Test
    void zeroMedianCanEstablishARealZeroBaseline() {
        measurement().put("reportedWeightGrams", 0);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("COMPLETED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals(0L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"TIMEOUT", "UNSTABLE"})
    void oldFailedMeasurementRemainsFailedWithItsActualValueKind(String status) {
        if (status.equals("TIMEOUT")) {
            measurement().put("status", status).put("weightValueKind", "NONE").put("weightValueAvailable", false)
                    .putNull("reportedWeightGrams").put("sampleCount", 0).put("sensorHealth", "TIMEOUT")
                    .put("faultCode", "WEIGHT_TIMEOUT");
        } else {
            measurement().put("weightValueKind", "LAST_FOUR_MEAN").put("faultCode", "WEIGHT_UNSTABLE");
        }
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("FAILED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals(measurement().path("weightValueKind").asText(), text("SELECT baseline_weight_value_kind FROM dev_physical_result"));
        assertEquals(measurement().path("faultCode").asText(), text("SELECT baseline_fault_code FROM dev_physical_result"));
        assertNull(number("SELECT baseline_total_weight_g FROM dev_physical_result"));
        assertEquals(status.equals("TIMEOUT") ? null : 1180L, number("SELECT baseline_last_observed_weight_g FROM dev_physical_result"));
        assertEquals(0, count("rec_port_weight_baseline"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"bag", "detection", "version", "content", "mcu", "legacy_mean", "legacy_failure"})
    void staleResultCannotChangeCurrentBagCapacityOrTare(String change) {
        switch (change) {
            case "bag" -> jdbc.update("UPDATE rec_port_capacity_state SET current_bag_id=2");
            case "detection" -> jdbc.update("UPDATE rec_port_capacity_state SET current_detection_id=2,detection_gate='IN_PROGRESS'");
            case "version" -> jdbc.update("UPDATE dev_device_runtime_state SET applied_config_version_no=9");
            case "content" -> jdbc.update("UPDATE dev_device_runtime_state SET applied_config_content_sha256=UNHEX(REPEAT('e',64))");
            case "mcu" -> jdbc.update("UPDATE dev_device_runtime_state SET applied_mcu_payload_sha256=UNHEX(REPEAT('e',64))");
            case "legacy_mean" -> {
                jdbc.update("UPDATE rec_port_capacity_state SET lock_version=8");
                measurement().put("status", "STABLE").put("weightValueKind", "STABLE_WINDOW_MEAN");
            }
            case "legacy_failure" -> {
                jdbc.update("UPDATE rec_port_capacity_state SET lock_version=8");
                measurement().put("weightValueKind", "LAST_FOUR_MEAN").put("faultCode", "WEIGHT_UNSTABLE");
            }
            default -> throw new AssertionError(change);
        }
        Map<String, Object> capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        Map<String, Object> tare = jdbc.queryForMap("SELECT * FROM dev_factory_installed_bag");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("STALE_IGNORED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals(1, count("dev_physical_result"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertSameDatabaseRow(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
        assertSameDatabaseRow(tare, jdbc.queryForMap("SELECT * FROM dev_factory_installed_bag"));
    }

    @Test
    void technicallyAbortedCommandKeepsItsReasonAndOnlyAddsLatePhysicalEvidence() {
        jdbc.update("UPDATE rec_port_baseline_measurement SET status='TECHNICAL_ABORTED',fault_code='UART_TIMEOUT',completed_at=NOW(3)");
        jdbc.update("UPDATE dev_device_command SET physical_state='PRE_START_FAILED',edge_accepted_at=NOW(3),physical_ended_at=NOW(3)");
        Map<String, Object> command = jdbc.queryForMap("SELECT * FROM dev_device_command");
        Map<String, Object> capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("STALE_IGNORED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals("UART_TIMEOUT", text("SELECT fault_code FROM rec_port_baseline_measurement"));
        assertEquals("TIMEOUT_MEDIAN", text("SELECT baseline_weight_value_kind FROM dev_physical_result"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertSameDatabaseRow(command, jdbc.queryForMap("SELECT * FROM dev_device_command"));
        assertSameDatabaseRow(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
        assertEquals("MEASURING", text("SELECT tare_status FROM dev_factory_installed_bag"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"empty", "port", "bag", "version", "content", "mcu", "calibration"})
    void frozenIntentAndEmptyBagConfirmationAreStillRequired(String field) {
        ObjectNode payload = (ObjectNode) normalized.path("event").path("payload");
        ObjectNode frozen = (ObjectNode) payload.path("frozenConfig");
        switch (field) {
            case "empty" -> payload.put("emptyBagConfirmed", false);
            case "port" -> payload.put("portNo", 1);
            case "bag" -> payload.put("bagUid", UUID.randomUUID().toString());
            case "version" -> frozen.put("version", 9);
            case "content" -> frozen.put("contentSha256", "e".repeat(64));
            case "mcu" -> frozen.put("mcuPayloadSha256", "e".repeat(64));
            case "calibration" -> measurement().put("calibrationVersion", 5);
            default -> throw new AssertionError(field);
        }
        assertThrows(UntrustedInboxSourceException.class, this::apply);
        assertInitialState();
    }

    @Test
    void lateMedianCannotInvalidateAnAlreadyEstablishedNewBaseline() {
        jdbc.update("""
                INSERT INTO rec_port_weight_baseline(id,tenant_id,organization_id,port_id,bag_id,version_no,
                    source_type,source_bag_event_id,baseline_weight_g,established_at,created_at)
                VALUES(9,1,1,1,1,2,'INITIAL_BINDING',9,1500,NOW(3),NOW(3))
                """);
        jdbc.update("""
                UPDATE rec_port_capacity_state SET baseline_state='VALID',current_baseline_id=9,
                    current_baseline_weight_g=1500,latest_stable_total_weight_g=1500,raw_net_weight_g=0,
                    displayed_fullness_percent=0,detection_gate='READY',confirmed_fullness_state='NOT_FULL',lock_version=8
                """);
        jdbc.update("UPDATE dev_factory_installed_bag SET tare_status='READY'");
        Map<String, Object> capacity = jdbc.queryForMap("SELECT * FROM rec_port_capacity_state");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("STALE_IGNORED", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals(1180L, number("SELECT baseline_total_weight_g FROM dev_physical_result"));
        assertEquals(1, count("rec_port_weight_baseline"));
        assertEquals(1500L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
        assertEquals("READY", text("SELECT tare_status FROM dev_factory_installed_bag"));
        assertSameDatabaseRow(capacity, jdbc.queryForMap("SELECT * FROM rec_port_capacity_state"));
    }

    private void assertInitialState() {
        for (String table : List.of("dev_edge_event", "dev_physical_result", "rec_port_weight_baseline", "p1av_effect")) {
            assertEquals(0, count(table), table);
        }
        assertEquals("PENDING", text("SELECT status FROM rec_port_baseline_measurement"));
        assertEquals("UNINITIALIZED", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertEquals(7L, number("SELECT lock_version FROM rec_port_capacity_state"));
        assertEquals("MEASURING", text("SELECT tare_status FROM dev_factory_installed_bag"));
        assertEquals("QUEUED", text("SELECT physical_state FROM dev_device_command"));
    }

    private static void assertSameDatabaseRow(Map<String, Object> expected, Map<String, Object> actual) {
        assertEquals(expected.keySet(), actual.keySet());
        expected.forEach((column, value) -> {
            if (value instanceof byte[] bytes) assertArrayEquals(bytes, (byte[]) actual.get(column), column);
            else assertEquals(value, actual.get(column), column);
        });
    }

    private TrustedDeviceEventApplyResult apply() {
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(normalized.path("event").path("payload"), Map.class);
        ((ObjectNode) normalized.path("event")).put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        return transaction.execute(status -> service.apply(new TrustedDeviceInboxEvent(
                inboxReferences.issue(1, 1, 1), "BASELINE_MEASUREMENT_COMPLETE", 2, mapper.writeValueAsString(normalized))));
    }

    private ObjectNode measurement() { return (ObjectNode) normalized.path("event").path("payload").path("totalWeightMeasurement"); }
    private int count(String table) { return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class); }
    private String text(String sql) { return jdbc.queryForObject(sql, String.class); }
    private Long number(String sql) { return jdbc.queryForObject(sql, Long.class); }

    private void prepareTables() {
        for (String table : List.of("dev_physical_result", "dev_edge_event", "rec_port_baseline_measurement",
                "rec_port_capacity_state", "rec_port_weight_baseline", "dev_factory_installed_bag",
                "dev_device_command", "dev_device_runtime_state")) {
            jdbc.execute("CREATE TABLE " + table + " LIKE ecobin_baseline_p1av." + table);
        }
        jdbc.execute("CREATE TABLE p1av_effect(kind VARCHAR(32)) ENGINE=InnoDB");
        jdbc.execute("CREATE TABLE dev_device_asset(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,hardware_sn VARCHAR(64),expected_port_count INT)");
        jdbc.execute("CREATE TABLE dev_port(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_no INT)");
        jdbc.execute("CREATE TABLE rec_bag(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,bag_uid VARCHAR(36),bag_code VARCHAR(64))");
        jdbc.execute("CREATE TABLE dev_config_version(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,version_no BIGINT,content_sha256 BINARY(32),mcu_payload_sha256 BINARY(32))");
        jdbc.execute("CREATE TABLE dev_port_config_snapshot(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,config_version_id BIGINT,port_id BIGINT,calibration_version BIGINT)");
        jdbc.update("INSERT INTO dev_device_asset VALUES(1,1,1,'SN-CONTRACT-0001',2)");
        jdbc.update("INSERT INTO dev_port VALUES(1,1,1,1,2)");
        jdbc.update("INSERT INTO rec_bag VALUES(1,1,1,'40000000-0000-4000-8000-000000000002','P1AV_BAG_01')");
        jdbc.update("INSERT INTO dev_config_version VALUES(1,1,1,1,8,UNHEX(REPEAT('a',64)),UNHEX(REPEAT('b',64)))");
        jdbc.update("INSERT INTO dev_port_config_snapshot VALUES(1,1,1,1,1,1,4)");
        jdbc.update("""
                INSERT INTO dev_device_runtime_state(asset_id,tenant_id,organization_id,edge_connection_status,
                    mcu_link_status,safety_status,aggregate_weight_health,camera_health,local_storage_health,
                    clock_sync_health,applied_config_version_no,applied_config_content_sha256,applied_mcu_payload_sha256,created_at,updated_at)
                VALUES(1,1,1,'UNKNOWN','UNKNOWN','SAFE','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN',8,
                    UNHEX(REPEAT('a',64)),UNHEX(REPEAT('b',64)),NOW(3),NOW(3))
                """);
        jdbc.update("""
                INSERT INTO rec_port_baseline_measurement(id,measurement_uid,tenant_id,organization_id,asset_id,
                    port_id,bag_id,device_config_version_id,port_config_snapshot_id,capacity_lock_version_snapshot,
                    fullness_rule_fingerprint,initiator_kind,status,started_at,created_at,updated_at)
                VALUES(1,'82000000-0000-4000-8000-000000000001',1,1,1,1,1,1,1,7,UNHEX(REPEAT('c',64)),
                    'SYSTEM','PENDING',NOW(3),NOW(3),NOW(3))
                """);
        jdbc.update("""
                INSERT INTO rec_port_capacity_state(port_id,tenant_id,organization_id,asset_id,baseline_state,
                    detection_gate,confirmed_fullness_state,current_bag_id,lock_version,updated_at)
                VALUES(1,1,1,1,'UNINITIALIZED','FAILED','UNKNOWN',1,7,NOW(3))
                """);
        jdbc.update("""
                INSERT INTO dev_factory_installed_bag(id,asset_id,port_no,bag_code,tare_status,installed_at,created_at,updated_at)
                VALUES(1,1,2,'P1AV_BAG_01','MEASURING',NOW(3),NOW(3),NOW(3))
                """);
        jdbc.update("""
                INSERT INTO dev_device_command(id,command_uid,tenant_id,organization_id,asset_id,command_type,
                    baseline_measurement_id,payload_schema_version,semantic_payload,semantic_payload_sha256,
                    physical_state,queued_at,created_at,updated_at)
                VALUES(1,'82000000-0000-4000-8000-000000000002',1,1,1,'MEASURE_EMPTY_BAG_BASELINE',1,2,
                    JSON_OBJECT(),UNHEX(REPEAT('d',64)),'QUEUED',NOW(3),NOW(3),NOW(3))
                """);
    }
}
