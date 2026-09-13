package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.application.target.ReliableEdgeConfirmationService;
import org.enveloping.ecobin.framework.reliability.*;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.enveloping.ecobin.recycling.application.clean.ApplyCleanCompleteService;
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

/** Real clean business service/transactions and migrated write-table CHECKs/unique keys.
 * Parent lookups are minimal fixtures; LIKE copies omit foreign keys/triggers. Reliable tasks
 * are transactional probes. Each case owns a disposable schema in the dedicated local container. */
@EnabledIfEnvironmentVariable(named = "ECOBIN_CLEAN_MEDIAN_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_median_p1at(?:\\?.*)?")
class CleanMedianTransactionTest {
    private final JsonMapper mapper = JsonMapper.builder().build();
    private SingleConnectionDataSource dataSource;
    private AnnotationConfigApplicationContext references;
    private JdbcTemplate jdbc;
    private TransactionTemplate transaction;
    private TrustedOrganizationInboxRefFactory inboxReferences;
    private ApplyCleanCompleteService service;
    private ObjectNode normalized;
    private String schema;
    private boolean failConfirmation;
    private boolean allowQuarantine;

    @BeforeEach
    void setUp() throws Exception {
        var connection = DriverManager.getConnection(System.getenv("ECOBIN_CLEAN_MEDIAN_MYSQL_URL"), "root", "");
        dataSource = new SingleConnectionDataSource(connection, true);
        assertEquals("ecobin_median_p1at", connection.getCatalog());
        assertTrue(connection.getMetaData().getDatabaseProductVersion().startsWith("8.4."));
        jdbc = new JdbcTemplate(dataSource);
        schema = "ecobin_p1at_case_" + UUID.randomUUID().toString().replace("-", "");
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
                        jdbc.update("INSERT INTO p1at_effect VALUES ('CONFIRMATION')");
                        if (failConfirmation) throw new IllegalStateException("injected confirmation failure");
                        return UUID.randomUUID();
                    }
                    public void cancelPending(DeviceAssetTaskRef source, String type, String target, String key) {
                        throw new AssertionError("no cancellation expected");
                    }
                });
        service = new ApplyCleanCompleteService(jdbc, mapper, new ReliableDeviceTaskProofPort() {
            public void completeFromTrustedProof(String type, String target, String key) {
                jdbc.update("INSERT INTO p1at_effect VALUES ('PROOF')");
            }
            public void completeDispatchFromTrustedCommandObservation(UUID command) {
                throw new AssertionError("not a command observation");
            }
        }, confirmation, (source, reason, diagnostic) -> {
            if (!allowQuarantine) throw new AssertionError(diagnostic);
            jdbc.update("INSERT INTO p1at_effect VALUES ('QUARANTINE')");
            return UUID.randomUUID();
        }, inboxReferences);
        prepareTables();
        Path root = Path.of("").toAbsolutePath().normalize();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/clean-complete.event.json")));
        ((ObjectNode) event.path("payload")).put("oldBagUid", "40000000-0000-4000-8000-000000000007");
        ObjectNode last = (ObjectNode) event.path("payload").path("cleanerConfirmedFinalMeasurement");
        last.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("sampleCount", 20).put("measurementElapsedMs", 5000);
        normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", "SN-CONTRACT-0001");
        // Synthetic trusted-inbox canonical identity; the real adapter has separate canonicalization tests.
        normalized.put("eventCanonicalSha256", "f".repeat(64)).set("event", event);
    }

    @AfterEach
    void close() {
        try {
            if (jdbc != null && schema != null) {
                assertTrue(schema.matches("ecobin_p1at_case_[0-9a-f]{32}"));
                jdbc.execute("DROP DATABASE `" + schema + "`");
            }
        } finally {
            if (references != null) references.close();
            if (dataSource != null) dataSource.destroy();
        }
    }

    @Test
    void medianCompletesCleanRecordAndNewBagBaselineOnce() {
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(TrustedDeviceEventApplyResult.NO_ACTION_REQUIRED, apply());
        assertEquals(1, count("dev_physical_result"));
        assertEquals(1, count("rec_clean_record"));
        assertEquals(0, count("rec_clean_anomaly"));
        assertEquals(1, count("rec_port_weight_baseline"));
        assertEquals(2, count("rec_bag_occupancy_event"));
        assertEquals(0, count("dev_device_occupancy"));
        assertEquals(2, count("p1at_effect"));
        assertEquals("COMPLETED", text("SELECT status FROM rec_clean_operation"));
        assertEquals("NORMAL", text("SELECT record_class FROM rec_clean_record"));
        assertEquals("RELIABLE", text("SELECT final_total_weight_status FROM rec_clean_record"));
        assertEquals(18800L, number("SELECT effective_removed_net_weight_g FROM rec_clean_record"));
        assertEquals(1200L, number("SELECT final_total_weight_g FROM rec_clean_record"));
        assertEquals("UNSTABLE", text("SELECT clean_final_measurement_status FROM dev_physical_result"));
        assertEquals("TIMEOUT_MEDIAN", text("SELECT clean_final_weight_value_kind FROM dev_physical_result"));
        assertEquals(1200L, number("SELECT clean_final_weight_g FROM dev_physical_result"));
        assertEquals(1200L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
        assertEquals(2L, number("SELECT bag_id FROM rec_port_weight_baseline"));
        assertEquals("VALID", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertEquals(2L, number("SELECT current_bag_id FROM rec_port_capacity_state"));
        assertEquals(2L, number("SELECT bag_id FROM rec_bag_current_occupancy"));
    }

    private TrustedDeviceEventApplyResult apply() {
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = mapper.convertValue(normalized.path("event").path("payload"), Map.class);
        ((ObjectNode) normalized.path("event")).put("payloadSha256", OneNetCanonicalJson.payloadSha256(payload));
        return transaction.execute(status -> service.apply(new TrustedDeviceInboxEvent(
                inboxReferences.issue(1, 1, 1), "CLEAN_COMPLETE", 2, mapper.writeValueAsString(normalized))));
    }

    @Test
    void failureAfterRecordBagSwapAndBaselineRollsBackEverythingThenRetriesOriginalFact() {
        failConfirmation = true;
        assertThrows(IllegalStateException.class, this::apply);
        for (String table : List.of("dev_physical_result", "dev_edge_event", "rec_clean_record", "rec_clean_anomaly",
                "rec_bag_occupancy_event", "rec_port_weight_baseline", "rec_port_capacity_state", "p1at_effect")) {
            assertEquals(0, count(table), table);
        }
        assertEquals(1, count("dev_device_occupancy"));
        assertEquals(2, count("rec_bag_current_occupancy"));
        assertEquals("PORT_BOUND", text("SELECT occupancy_type FROM rec_bag_current_occupancy WHERE bag_id=1"));
        assertEquals("CLEAN_RESERVED", text("SELECT occupancy_type FROM rec_bag_current_occupancy WHERE bag_id=2"));
        assertEquals("IN_PROGRESS", text("SELECT status FROM rec_clean_operation"));
        assertEquals("PHYSICAL_STARTED", text("SELECT physical_state FROM dev_device_command"));
        assertEquals(0L, number("SELECT last_visibility_sequence_no FROM rec_organization_clean_record_counter"));
        failConfirmation = false;
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(1, count("rec_clean_record"));
        assertEquals(1, count("rec_port_weight_baseline"));
        assertEquals(0, count("dev_device_occupancy"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"pre", "both", "legacy"})
    void firstAndFinalKindsArePreservedAndLegacyMeanStillWorks(String slot) {
        ObjectNode before = measurement("preUnlockMeasurement");
        ObjectNode after = measurement("cleanerConfirmedFinalMeasurement");
        if (!slot.equals("legacy")) setMedian(before);
        if (!slot.equals("both")) {
            after.put("status", "STABLE").put("weightValueKind", "STABLE_WINDOW_MEAN")
                    .put("measurementElapsedMs", 0).put("sampleCount", 1);
        }
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(before.path("status").asText(), text("SELECT clean_pre_measurement_status FROM dev_physical_result"));
        assertEquals(before.path("weightValueKind").asText(), text("SELECT clean_pre_weight_value_kind FROM dev_physical_result"));
        assertEquals(after.path("status").asText(), text("SELECT clean_final_measurement_status FROM dev_physical_result"));
        assertEquals(after.path("weightValueKind").asText(), text("SELECT clean_final_weight_value_kind FROM dev_physical_result"));
        assertEquals("NORMAL", text("SELECT record_class FROM rec_clean_record"));
        assertEquals(1200L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
    }

    @Test
    void genuineFinalTimeoutStaysAnomalyWithoutInventingBaseline() {
        measurement("cleanerConfirmedFinalMeasurement").put("status", "TIMEOUT").put("weightValueKind", "NONE")
                .put("weightValueAvailable", false).putNull("reportedWeightGrams").put("sampleCount", 0)
                .put("sensorHealth", "TIMEOUT").put("faultCode", "WEIGHT_TIMEOUT");
        payload().putNull("newBaselineWeightGrams").putNull("removedNetWeightGrams");
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals("SYSTEM_ANOMALY", text("SELECT record_class FROM rec_clean_record"));
        assertEquals("FAILED", text("SELECT final_total_weight_status FROM rec_clean_record"));
        assertNull(number("SELECT final_total_weight_g FROM rec_clean_record"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertEquals("INVALID", text("SELECT baseline_state FROM rec_port_capacity_state"));
        assertEquals(java.util.Set.of("NEW_BASELINE_FAILED", "DEVICE_REMOVED_WEIGHT_UNAVAILABLE",
                        "RECALCULATED_REMOVED_WEIGHT_UNAVAILABLE"),
                java.util.Set.copyOf(jdbc.queryForList("SELECT anomaly_code FROM rec_clean_anomaly", String.class)));
        assertEquals(2L, number("SELECT bag_id FROM rec_bag_current_occupancy"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"different", "missing"})
    void currentBeforeAfterDifferenceDoesNotDependOnTheOldBagBaseline(String old) {
        if (old.equals("different")) {
            jdbc.update("UPDATE rec_clean_operation SET old_baseline_weight_g=80");
        } else {
            jdbc.update("UPDATE rec_clean_operation SET old_baseline_state='MISSING',old_baseline_id=NULL,old_baseline_weight_g=NULL");
        }
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        assertEquals(18800L, number("SELECT effective_removed_net_weight_g FROM rec_clean_record"));
        assertEquals("NORMAL", text("SELECT record_class FROM rec_clean_record"));
        assertEquals(1200L, number("SELECT baseline_weight_g FROM rec_port_weight_baseline"));
        assertEquals(0, count("rec_clean_anomaly"));
        assertEquals(0, count("dev_device_occupancy"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"preUnlockMeasurement", "cleanerConfirmedFinalMeasurement"})
    void nativeMeasurementIdentityIsSavedWithoutReplacingItsOriginalBytes(String slot) {
        ObjectNode weight = measurement(slot);
        String hex = "45424d31" + String.format(java.util.Locale.ROOT, "%016x%08x",
                weight.path("mcuBootId").asLong(), weight.path("mcuEventSequence").asLong());
        String uid = hex.substring(0, 8) + "-" + hex.substring(8, 12) + "-" + hex.substring(12, 16)
                + "-" + hex.substring(16, 20) + "-" + hex.substring(20);
        weight.put("measurementUid", uid);
        assertEquals(TrustedDeviceEventApplyResult.APPLIED, apply());
        String column = slot.equals("preUnlockMeasurement") ? "clean_pre_measurement_uid" : "clean_final_measurement_uid";
        assertEquals(uid, text("SELECT " + column + " FROM dev_physical_result"));
        assertEquals("NORMAL", text("SELECT record_class FROM rec_clean_record"));
    }

    private ObjectNode payload() { return (ObjectNode) normalized.path("event").path("payload"); }
    private ObjectNode measurement(String slot) { return (ObjectNode) payload().path(slot); }
    private static void setMedian(ObjectNode node) {
        node.put("status", "UNSTABLE").put("weightValueKind", "TIMEOUT_MEDIAN")
                .put("measurementElapsedMs", 5000).put("sampleCount", 20);
    }

    @ParameterizedTest
    @MethodSource("invalidMedianFields")
    void invalidMedianDoesNotCreateRecordSwapBagsOrReleaseDevice(String slot, String field, Object value) {
        ObjectNode weight = measurement(slot);
        setMedian(weight);
        weight.set(field, mapper.valueToTree(value));
        assertThrows(IllegalArgumentException.class, this::apply);
        assertUnchangedClean();
    }

    private static Stream<Arguments> invalidMedianFields() {
        Object[][] invalid = {
                {"measurementUid", null}, {"status", "STABLE"}, {"weightValueAvailable", false},
                {"reportedWeightGrams", null}, {"reportedWeightGrams", 2147483648L},
                {"reportedWeightGrams", -2147483649L}, {"measurementElapsedMs", 4999},
                {"measurementElapsedMs", 5001}, {"sampleCount", 4}, {"sampleCount", 33},
                {"calibrationVersion", 4294967296L}, {"sensorHealth", "UNKNOWN"},
                {"faultCode", "WEIGHT_UNSTABLE"}, {"mcuBootId", 0},
                {"mcuBootId", 9007199254740992L}, {"mcuEventSequence", 0}, {"mcuEventSequence", 4294967296L}
        };
        return Stream.of("preUnlockMeasurement", "cleanerConfirmedFinalMeasurement").flatMap(slot ->
                Stream.of(invalid).map(pair -> Arguments.of(slot, pair[0], pair[1])));
    }

    @ParameterizedTest
    @ValueSource(strings = {"completion", "physicalClose", "lockPower", "baselineMissing", "baselineMismatch"})
    void medianCannotBypassHumanConfirmationOrSupplyAnotherBaseline(String mismatch) {
        switch (mismatch) {
            case "completion" -> payload().put("cleanerCompletionConfirmed", false);
            case "physicalClose" -> ((ObjectNode) payload().path("cleanLockAndManualDoorConfirmation"))
                    .put("cleanerPhysicalCloseConfirmed", false);
            case "lockPower" -> ((ObjectNode) payload().path("cleanLockAndManualDoorConfirmation"))
                    .put("lockPowerState", "ENERGIZED");
            case "baselineMissing" -> payload().putNull("newBaselineWeightGrams");
            case "baselineMismatch" -> payload().put("newBaselineWeightGrams", 1201);
            default -> throw new AssertionError(mismatch);
        }
        assertThrows(IllegalArgumentException.class, this::apply);
        assertUnchangedClean();
    }

    @ParameterizedTest
    @ValueSource(strings = {"calibration", "minimum", "maximum"})
    void medianStillMustMatchFrozenOperationConfiguration(String mismatch) {
        allowQuarantine = true;
        ObjectNode last = measurement("cleanerConfirmedFinalMeasurement");
        switch (mismatch) {
            case "calibration" -> last.put("calibrationVersion", 5);
            case "minimum" -> last.put("reportedWeightGrams", -1);
            case "maximum" -> last.put("reportedWeightGrams", 350001);
            default -> throw new AssertionError(mismatch);
        }
        payload().set("newBaselineWeightGrams", last.get("reportedWeightGrams"));
        assertEquals(TrustedDeviceEventApplyResult.QUARANTINED, apply());
        assertUnchangedClean();
        assertEquals(2, count("p1at_effect"));
    }

    private void assertUnchangedClean() {
        assertEquals(0, count("dev_physical_result"));
        assertEquals(0, count("rec_clean_record"));
        assertEquals(0, count("rec_port_weight_baseline"));
        assertEquals(0, count("rec_bag_occupancy_event"));
        assertEquals(1, count("dev_device_occupancy"));
        assertEquals("IN_PROGRESS", text("SELECT status FROM rec_clean_operation"));
        assertEquals("PORT_BOUND", text("SELECT occupancy_type FROM rec_bag_current_occupancy WHERE bag_id=1"));
        assertEquals("CLEAN_RESERVED", text("SELECT occupancy_type FROM rec_bag_current_occupancy WHERE bag_id=2"));
    }

    private int count(String table) { return jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class); }
    private String text(String sql) { return jdbc.queryForObject(sql, String.class); }
    private Long number(String sql) { return jdbc.queryForObject(sql, Long.class); }

    private void prepareTables() {
        for (String table : List.of("dev_physical_result", "dev_edge_event", "rec_clean_record", "rec_clean_anomaly",
                "rec_clean_photo", "rec_bag_current_occupancy", "rec_bag_occupancy_event", "rec_port_weight_baseline",
                "rec_port_capacity_state", "rec_port_clean_restart_interlock", "rec_organization_clean_record_counter")) {
            jdbc.execute("CREATE TABLE " + table + " LIKE ecobin_median_p1at." + table);
        }
        jdbc.execute("CREATE TABLE p1at_effect (kind VARCHAR(32)) ENGINE=InnoDB");
        jdbc.execute("CREATE TABLE dev_device_asset (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,hardware_sn VARCHAR(64))");
        jdbc.execute("INSERT INTO dev_device_asset VALUES (1,1,1,'SN-CONTRACT-0001')");
        jdbc.execute("CREATE TABLE dev_device_runtime_state (asset_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,last_device_event_at DATETIME(3),lock_version INT DEFAULT 0,updated_at DATETIME(3))");
        jdbc.execute("INSERT INTO dev_device_runtime_state (asset_id,tenant_id,organization_id) VALUES (1,1,1)");
        jdbc.execute("CREATE TABLE dev_port_runtime_state (port_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,pending_delivery_result_session_id BIGINT,lock_version INT DEFAULT 0,updated_at DATETIME(3))");
        jdbc.execute("INSERT INTO dev_port_runtime_state (port_id,tenant_id,organization_id,asset_id) VALUES (1,1,1,1)");
        jdbc.execute("CREATE TABLE dev_port (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,port_no INT)");
        jdbc.execute("INSERT INTO dev_port VALUES (1,1,1,1,2)");
        jdbc.execute("CREATE TABLE dev_config_version (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,version_no BIGINT,content_sha256 BINARY(32),mcu_payload_sha256 BINARY(32))");
        jdbc.execute("INSERT INTO dev_config_version VALUES (1,1,1,1,8,UNHEX(REPEAT('aa',32)),UNHEX(REPEAT('bb',32)))");
        jdbc.execute("""
                CREATE TABLE dev_port_config_snapshot (
                  id BIGINT PRIMARY KEY, config_version_id BIGINT, port_id BIGINT,
                  tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT,
                  fullness_mode VARCHAR(32), configured_full_weight_g BIGINT,
                  fullness_settle_wait_ms BIGINT, fullness_confirmation_wait_ms BIGINT,
                  weight_measurement_timeout_ms BIGINT, weight_minimum_g BIGINT,
                  weight_maximum_g BIGINT, calibration_version BIGINT)
                """);
        jdbc.execute("INSERT INTO dev_port_config_snapshot VALUES (1,1,1,1,1,1,'WEIGHT_ONLY',50000,0,0,5000,0,350000,4)");
        jdbc.execute("CREATE TABLE rec_bag (id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,bag_uid CHAR(36))");
        jdbc.execute("INSERT INTO rec_bag VALUES (1,1,1,'40000000-0000-4000-8000-000000000007'),(2,1,1,'40000000-0000-4000-8000-000000000002')");
        jdbc.execute("""
                CREATE TABLE rec_clean_operation (
                  id BIGINT PRIMARY KEY,operation_uid CHAR(36),tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,
                  port_id BIGINT,cleaner_organization_user_id BIGINT,device_config_version_id BIGINT,
                  clean_config_version_id BIGINT,clean_config_version_no BIGINT,
                  old_bag_binding_state VARCHAR(32),old_bag_id BIGINT,old_bag_code_snapshot VARCHAR(64),
                  old_baseline_state VARCHAR(32),old_baseline_id BIGINT,old_baseline_weight_g BIGINT,
                  new_bag_id BIGINT,new_bag_code_snapshot VARCHAR(64),pending_delivery_result_session_id BIGINT,
                  status VARCHAR(32),completion_record_id BIGINT,edge_saved_confirmed TINYINT DEFAULT 1,
                  first_unlock_may_have_executed TINYINT DEFAULT 1,clean_lock_deenergized_confirmed TINYINT DEFAULT 0,
                  cleaner_physical_close_confirmed TINYINT DEFAULT 0,pre_unlock_weight_status VARCHAR(32),
                  pre_unlock_weight_g BIGINT,pre_unlock_weight_fault_code VARCHAR(32),ended_at DATETIME(3),
                  end_reason VARCHAR(32),lock_version INT DEFAULT 0,updated_at DATETIME(3))
                """);
        jdbc.execute("""
                INSERT INTO rec_clean_operation (id,operation_uid,tenant_id,organization_id,asset_id,port_id,
                  cleaner_organization_user_id,device_config_version_id,clean_config_version_id,clean_config_version_no,
                  old_bag_binding_state,old_bag_id,old_bag_code_snapshot,old_baseline_state,old_baseline_id,
                  old_baseline_weight_g,new_bag_id,new_bag_code_snapshot,status)
                VALUES (1,'40000000-0000-4000-8000-000000000001',1,1,1,1,1,1,1,1,
                  'BOUND',1,'OLD-P1AT','TRUSTED',1,1200,2,'NEW-P1AT','IN_PROGRESS')
                """);
        jdbc.execute("CREATE TABLE dev_device_occupancy (asset_id BIGINT PRIMARY KEY,tenant_id BIGINT,organization_id BIGINT,occupancy_kind VARCHAR(32),clean_operation_id BIGINT)");
        jdbc.execute("INSERT INTO dev_device_occupancy VALUES (1,1,1,'CLEAN',1)");
        jdbc.execute("""
                CREATE TABLE dev_device_command (
                  id BIGINT PRIMARY KEY,command_uid CHAR(36),tenant_id BIGINT,organization_id BIGINT,asset_id BIGINT,
                  command_type VARCHAR(32),clean_operation_id BIGINT,physical_state VARCHAR(32),
                  edge_accepted_at DATETIME(3),physical_started_at DATETIME(3),physical_ended_at DATETIME(3),
                  lock_version INT DEFAULT 0,updated_at DATETIME(3))
                """);
        jdbc.execute("INSERT INTO dev_device_command (id,command_uid,tenant_id,organization_id,asset_id,command_type,clean_operation_id,physical_state) VALUES (1,'40000000-0000-4000-8000-000000000005',1,1,1,'START_CLEAN_OPERATION',1,'PHYSICAL_STARTED')");
        jdbc.execute("INSERT INTO rec_bag_current_occupancy (bag_id,tenant_id,organization_id,occupancy_type,port_id,clean_operation_id,acquired_at) VALUES (1,1,1,'PORT_BOUND',1,NULL,UTC_TIMESTAMP(3)),(2,1,1,'CLEAN_RESERVED',NULL,1,UTC_TIMESTAMP(3))");
        jdbc.execute("INSERT INTO rec_organization_clean_record_counter (organization_id,tenant_id,updated_at) VALUES (1,1,UTC_TIMESTAMP(3))");
        for (String position : List.of("FIRST_OPEN_INNER", "FIRST_OPEN_OUTER", "FINAL_CLOSE_INNER", "FINAL_CLOSE_OUTER")) {
            jdbc.update("INSERT INTO rec_clean_photo (tenant_id,organization_id,clean_operation_id,position,status,missing_reason,created_at,updated_at) VALUES (1,1,1,?,'UPLOAD_PENDING','CAMERA_NOT_READY',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", position);
        }
    }
}
