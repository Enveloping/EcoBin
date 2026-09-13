package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.TrustedOrangePiRuntimeFactService;
import org.enveloping.ecobin.device.application.target.TrustedMcuFirmwareIdentityService;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRefFactory;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
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

/** Actual public runtime receiver and MySQL transaction. LIKE write-table copies retain CHECKs
 * and unique keys, not foreign keys/triggers. Minimal parent fixtures, no workflow/funds ports. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_RUNTIME_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1au(?:\\?.*)?")
class RuntimeMedianTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private AnnotationConfigApplicationContext references;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private TrustedOrganizationInboxRefFactory inboxReferences;
    private TrustedOrangePiRuntimeFactService service;
    private ObjectNode normalized;
    private String schema;
    private long inboxId = 1;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_RUNTIME_MEDIAN_MYSQL_URL"), "root", "");
        dataSource = new SingleConnectionDataSource(connection, true);
        assertEquals("ecobin_median_p1au", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        jdbc = new JdbcTemplate(dataSource);
        schema = "ecobin_p1au_case_" + UUID.randomUUID().toString().replace("-", "");
        jdbc.execute("CREATE DATABASE `" + schema + "`");
        connection.setCatalog(schema);
        transaction = new TransactionTemplate(new DataSourceTransactionManager(dataSource));
        references = new AnnotationConfigApplicationContext();
        var scanner = new ClassPathBeanDefinitionScanner(references, false);
        scanner.addIncludeFilter(new AssignableTypeFilter(TrustedOrganizationInboxRefFactory.class));
        scanner.scan("org.enveloping.ecobin.framework.reliability");
        references.refresh();
        inboxReferences = references.getBean(TrustedOrganizationInboxRefFactory.class);
        // Snapshot-only entry point must not call unrelated reliable-fact/business collaborators.
        service = new TrustedOrangePiRuntimeFactService(jdbc, mapper, null, null,
                (source, reason, diagnostic) -> { throw new AssertionError(diagnostic); }, inboxReferences,
                null, null, null, null, new TrustedMcuFirmwareIdentityService(jdbc), List.of(), List.of());
        prepareTables();
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/device-runtime-snapshot.event.json")));
        ((ObjectNode) event.path("payload")).remove("mcuFirmwareIdentity");
        normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", "SN-CONTRACT-0001");
        normalized.put("eventCanonicalSha256", "f".repeat(64)).set("event", event);
        port(0).put("weightMeasurementStatus", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("weightSampleCount", 20).put("measurementElapsedMs", 5000);
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertTrue(schema.matches("ecobin_p1au_case_[0-9a-f]{32}"));
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (references != null) references.close();
            if (dataSource != null) dataSource.destroy();
        }
    }

    @Test
    void runtimeReceiverPreservesMedianQualityAndActualMeasurementIdentity() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        Map<String, Object> row = runtime(1);
        assertEquals("UNSTABLE", row.get("weight_measurement_status"));
        assertEquals("TIMEOUT_MEDIAN", row.get("weight_value_kind"));
        assertEquals(13250L, ((Number) row.get("reported_weight_grams")).longValue());
        assertEquals(101L, row.get("weight_mcu_boot_id"));
        assertEquals(51L, row.get("weight_mcu_event_sequence"));
        assertNull(row.get("weight_fault_code"));
        assertEquals("STABLE_WINDOW_MEAN", runtime(2).get("weight_value_kind"));
        assertEquals(52L, runtime(2).get("weight_mcu_event_sequence"));
        assertEquals("OK", jdbc.queryForObject("SELECT aggregate_weight_health FROM dev_device_runtime_state", String.class));
        assertEquals("SAFE", jdbc.queryForObject("SELECT safety_status FROM dev_device_runtime_state", String.class));
    }

    @Test
    void medianWithOmittedFaultEvidenceIsRejectedBeforeChangingCurrentRuntime() {
        port(0).remove("weightFaultCode");
        assertThrows(IllegalArgumentException.class, this::apply);
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM dev_edge_event", Integer.class));
        assertNull(runtime(1).get("weight_value_kind"));
        assertNull(runtime(2).get("weight_value_kind"));
    }

    @ParameterizedTest
    @MethodSource("invalidMedian")
    void rejectsInvalidMedianFromActualReceiverWithoutPartiallyUpdatingTwoPorts(String field, Object value) {
        port(1).put("weightMeasurementStatus", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("weightSampleCount", 20).put("measurementElapsedMs", 5000);
        port(1).set(field, mapper.valueToTree(value));
        assertThrows(IllegalArgumentException.class, this::apply);
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM dev_edge_event", Integer.class));
        assertNull(runtime(1).get("weight_value_kind"));
        assertNull(runtime(2).get("weight_value_kind"));
        assertNull(jdbc.queryForObject("SELECT trusted_runtime_sequence FROM dev_device_runtime_state", Long.class));
    }

    static Stream<Arguments> invalidMedian() {
        return Stream.of(
                Arguments.of("weightMeasurementStatus", "STABLE"), Arguments.of("weightMeasurementStatus", null),
                Arguments.of("weightValueAvailable", false), Arguments.of("weightValueAvailable", null),
                Arguments.of("reportedWeightGrams", null), Arguments.of("reportedWeightGrams", 2147483648L),
                Arguments.of("reportedWeightGrams", -2147483649L), Arguments.of("reportedWeightGrams", 0.5),
                Arguments.of("measurementElapsedMs", 4999), Arguments.of("measurementElapsedMs", 5001),
                Arguments.of("measurementElapsedMs", null), Arguments.of("weightSampleCount", 4),
                Arguments.of("weightSampleCount", 33), Arguments.of("weightSampleCount", null),
                Arguments.of("weightSensorHealth", "TIMEOUT"), Arguments.of("weightSensorHealth", null),
                Arguments.of("weightFaultCode", "WEIGHT_UNSTABLE"), Arguments.of("weightFaultCode", ""),
                Arguments.of("calibrationVersion", -1), Arguments.of("calibrationVersion", 4294967296L),
                Arguments.of("calibrationVersion", null), Arguments.of("weightMcuBootId", 0),
                Arguments.of("weightMcuBootId", 9007199254740992L), Arguments.of("weightMcuBootId", null),
                Arguments.of("weightMcuEventSequence", 0), Arguments.of("weightMcuEventSequence", 4294967296L),
                Arguments.of("weightMcuEventSequence", null), Arguments.of("weightMeasurementUid", null),
                Arguments.of("weightMeasurementUid", "not-a-uuid"),
                Arguments.of("weightMeasurementUid", "89500000-0000-1000-8000-000000000001"));
    }

    @Test
    void duplicateSnapshotDoesNotRewriteCurrentProjection() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        Map<String, Object> before = runtime(1);
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, apply());
        assertEquals(before, runtime(1));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM dev_edge_event", Integer.class));
    }

    @Test
    void olderSnapshotIsRecordedWithoutRegressingWeightOrMeasurementIdentity() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        Map<String, Object> before = runtime(1);
        nextSnapshot(1052);
        port(0).put("reportedWeightGrams", 1).put("weightMcuBootId", 100).put("weightMcuEventSequence", 2);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(before, runtime(1));
        assertEquals(2, jdbc.queryForObject("SELECT COUNT(*) FROM dev_edge_event", Integer.class));
        assertEquals(1053L, jdbc.queryForObject("SELECT trusted_runtime_sequence FROM dev_device_runtime_state", Long.class));
    }

    @Test
    void lateSecondPortFailureRollsBackDeviceAndFirstPortUpdatesAndCanRetry() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        Map<String, Object> before = runtime(1);
        Map<String, Object> deviceBefore = jdbc.queryForMap("SELECT * FROM dev_device_runtime_state");
        nextSnapshot(1054);
        port(0).put("reportedWeightGrams", 17000);
        port(1).put("faultBitmap", -1);
        assertThrows(IllegalArgumentException.class, this::apply);
        assertEquals(before, runtime(1));
        Map<String, Object> deviceAfter = jdbc.queryForMap("SELECT * FROM dev_device_runtime_state");
        assertEquals(deviceBefore.keySet(), deviceAfter.keySet());
        deviceBefore.forEach((column, value) -> {
            if (value instanceof byte[] bytes) assertArrayEquals(bytes, (byte[]) deviceAfter.get(column), column);
            else assertEquals(value, deviceAfter.get(column), column);
        });
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM dev_edge_event", Integer.class));
        port(1).put("faultBitmap", 0);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(17000L, ((Number) runtime(1).get("reported_weight_grams")).longValue());
    }

    @Test
    void actualTimeoutRetainsFaultAndClearsValueInsteadOfReusingPreviousMedian() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        nextSnapshot(1054);
        port(0).put("weightMeasurementStatus", "TIMEOUT").put("weightValueKind", "NONE")
                .put("weightValueAvailable", false).putNull("reportedWeightGrams").put("weightSampleCount", 0)
                .put("weightSensorHealth", "TIMEOUT").put("weightFaultCode", "WEIGHT_TIMEOUT")
                .put("weightMcuEventSequence", 61);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertNull(runtime(1).get("reported_weight_grams"));
        assertEquals("WEIGHT_TIMEOUT", runtime(1).get("weight_fault_code"));
        assertEquals(61L, runtime(1).get("weight_mcu_event_sequence"));
        assertEquals("TIMEOUT", runtime(1).get("weight_sensor_health"));
    }

    @Test
    void legacyMeanDoesNotInventMissingWeightIdentity() {
        port(0).put("weightMeasurementStatus", "STABLE").put("weightValueKind", "STABLE_WINDOW_MEAN")
                .put("weightSampleCount", 1).put("measurementElapsedMs", 0);
        port(0).remove("weightMcuBootId");
        port(0).remove("weightMcuEventSequence");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertNull(runtime(1).get("weight_mcu_boot_id"));
        assertNull(runtime(1).get("weight_mcu_event_sequence"));
    }

    @Test
    void usableMedianDoesNotClearSmokeAlarmOrForgeCloudConnectivity() {
        jdbc.update("UPDATE dev_device_transport_state SET onenet_connection_status='OFFLINE'");
        port(0).put("smokeState", "ALARM");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("SAFETY_BLOCKED", runtime(1).get("safety_status"));
        assertEquals("SAFETY_BLOCKED", jdbc.queryForObject("SELECT safety_status FROM dev_device_runtime_state", String.class));
        assertEquals("OFFLINE", jdbc.queryForObject("SELECT edge_connection_status FROM dev_device_runtime_state", String.class));
        assertEquals("TIMEOUT_MEDIAN", runtime(1).get("weight_value_kind"));
    }

    private void nextSnapshot(long sequence) {
        inboxId++;
        ((ObjectNode) normalized.path("event")).put("eventUid", UUID.randomUUID().toString())
                .put("edgeEventSequence", sequence);
        normalized.put("eventCanonicalSha256", "e".repeat(64));
    }

    private TrustedDeviceEventApplyResult apply() {
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(normalized.path("event").path("payload"), Map.class);
        ((ObjectNode) normalized.path("event")).put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        return transaction.execute(status -> service.apply(new TrustedDeviceInboxEvent(
                inboxReferences.issue(inboxId, 1, 1), "DEVICE_RUNTIME_SNAPSHOT", 2, mapper.writeValueAsString(normalized))));
    }

    private ObjectNode port(int index) { return (ObjectNode) normalized.path("event").path("payload").path("ports").get(index); }

    private Map<String, Object> runtime(int port) {
        return jdbc.queryForMap("SELECT * FROM dev_port_runtime_state WHERE port_id=?", port);
    }

    private void prepareTables() {
        for (String table : List.of("dev_edge_event", "dev_device_runtime_state", "dev_port_runtime_state", "dev_device_fault_event")) {
            jdbc.execute("CREATE TABLE " + table + " LIKE ecobin_median_p1au." + table);
        }
        jdbc.execute("CREATE TABLE dev_device_asset(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,hardware_sn VARCHAR(64),expected_port_count INT)");
        jdbc.execute("CREATE TABLE dev_port(id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_no INT)");
        jdbc.execute("CREATE TABLE dev_device_transport_state(asset_id BIGINT PRIMARY KEY,onenet_connection_status VARCHAR(16))");
        jdbc.update("INSERT INTO dev_device_asset VALUES(1,1,1,'SN-CONTRACT-0001',2)");
        jdbc.update("INSERT INTO dev_port VALUES(1,1,1,1,1),(2,1,1,1,2)");
        jdbc.update("INSERT INTO dev_device_transport_state VALUES(1,'ONLINE')");
        jdbc.update("""
                INSERT INTO dev_device_runtime_state(asset_id,tenant_id,organization_id,edge_connection_status,
                    mcu_link_status,safety_status,aggregate_weight_health,camera_health,local_storage_health,
                    clock_sync_health,created_at,updated_at)
                VALUES(1,1,1,'UNKNOWN','UNKNOWN','SAFE','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN',NOW(3),NOW(3))
                """);
        for (int port = 1; port <= 2; port++) jdbc.update("""
                INSERT INTO dev_port_runtime_state(port_id,tenant_id,organization_id,asset_id,delivery_door_state,
                    delivery_door_actuator_health,delivery_door_contact_state,clean_lock_power_state,
                    clean_solenoid_health,clean_door_inferred_state,clean_door_state_basis,weight_sensor_health,
                    infrared_value,infrared_sensor_health,smoke_state,smoke_sensor_health,safety_status,created_at,updated_at)
                VALUES(?,1,1,1,'UNKNOWN','UNKNOWN','UNAVAILABLE','DEENERGIZED','UNKNOWN','UNKNOWN','NOT_OBSERVABLE',
                    'UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN','SAFE',NOW(3),NOW(3))
                """, port);
    }
}
