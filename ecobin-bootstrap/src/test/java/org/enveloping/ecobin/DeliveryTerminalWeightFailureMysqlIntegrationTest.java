package org.enveloping.ecobin;

import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.application.target.TrustedDeviceSourceScopeService;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.enveloping.ecobin.operations.api.inbox.*;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/** Full migrated schema, runtime grants and real inbox -> device -> order -> confirmation.
 * Only a disposable loopback MySQL is permitted; all external dispatch is disabled. */
@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_ISSUE_RELIABLE_MYSQL_URL}",
        "spring.datasource.username=ecobin_app", "spring.datasource.password=",
        "spring.sql.init.mode=never", "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake", "ecobin.external.fake.block-inbound=true",
        "ecobin.operations.reliable.workers-enabled=false",
        "ecobin.device.activation.scheduler-enabled=false",
        "ecobin.device.delivery-observation-reconciliation.scheduler-enabled=false",
        "ecobin.identity.default-platform-admin.enabled=false",
        "ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false",
        "onenet.subscription.enabled=false",
        "bagCodeKeyK1=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "jwt.secret=LOCAL_TERMINAL_WEIGHT_TEST_SECRET_AT_LEAST_32_BYTES"
})
@EnabledIfEnvironmentVariable(named = "ECOBIN_ISSUE_RELIABLE_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_issue_p1bl")
class DeliveryTerminalWeightFailureMysqlIntegrationTest {
    @Autowired JdbcTemplate jdbc;
    @Autowired ObjectMapper mapper;
    @Autowired TrustedInboxPort inbox;
    @Autowired ReliableDeviceInboxWorkerPort worker;
    @Autowired ReliableDeviceTaskRegistrationPort registration;
    @Autowired DeviceCommandTaskRefFactory commandRefs;
    @Autowired PlatformTransactionManager transactions;
    @Autowired TrustedDeviceSourceScopeService sourceScopes;

    @Test
    void terminalTimeoutCreatesOnlyPendingSystemAnomalyAndOriginalConfirmation() throws Exception {
        var test = seed();
        var receipt = inbox.receive(message(test));
        assertEquals(TrustedInboxReceiptState.ACCEPTED, receipt.state());
        var applied = worker.runBatch("terminal-weight-failure");
        assertTrue(applied.accepted() >= 1);
        assertEquals("PROCESSED", jdbc.queryForObject("SELECT processing_state FROM ops_inbox_message WHERE inbox_uid=?", String.class, receipt.inboxUid().toString()));
        var order = jdbc.queryForMap("SELECT * FROM rec_delivery_order WHERE delivery_session_id=?", test.fixture.session);
        assertEquals("PENDING", order.get("review_status"));
        assertEquals("RELIABLE", order.get("initial_weight_status"));
        assertEquals(12000L, ((Number) order.get("initial_weight_g")).longValue());
        assertEquals("UNAVAILABLE", order.get("final_weight_status"));
        for (String field : new String[]{"final_weight_g", "raw_net_weight_g", "raw_business_weight_kg",
                "raw_amount_cent", "final_amount_cent", "first_approved_at"})
            assertNull(order.get(field), field);
        // The due time is the immutable configuration snapshot required by the
        // existing schema. UNAVAILABLE results still create no automatic-review task.
        assertEquals(order.get("backend_received_at"), order.get("automatic_review_due_at"));
        assertEquals("UNAVAILABLE", order.get("raw_calculation_status"));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM rec_delivery_anomaly WHERE delivery_order_id=?", Integer.class, order.get("id")));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM rec_delivery_anomaly WHERE delivery_order_id=? AND category='SYSTEM' AND anomaly_code='TERMINAL_WEIGHT_FAILURE'",
                Integer.class, order.get("id")));
        assertEquals("PHYSICAL_FAILED", jdbc.queryForObject("SELECT physical_state FROM dev_device_command WHERE id=?", String.class, test.fixture.command));
        assertEquals("BUSINESS_CONFIRMED|TERMINAL_WEIGHT_FAILURE", jdbc.queryForObject(
                "SELECT CONCAT(status,'|',end_reason) FROM dev_delivery_session WHERE id=?", String.class, test.fixture.session));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id=?", Integer.class, test.fixture.asset));
        assertEquals("DONE", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?", String.class, test.originalTask.toString()));
        assertNoFundsOrAutomaticReview(test);
        assertEquals(1111L, jdbc.queryForObject("SELECT latest_stable_total_weight_g FROM rec_port_capacity_state WHERE asset_id=?", Long.class, test.fixture.asset));
        assertEquals(0L, jdbc.queryForObject("SELECT lock_version FROM rec_port_capacity_state WHERE asset_id=?", Long.class, test.fixture.asset));

        String key = "CONFIRM_EDGE_EVENT:" + test.event.path("eventUid").asText().toUpperCase(java.util.Locale.ROOT);
        var confirmation = mapper.readTree(jdbc.queryForObject(
                "SELECT redacted_execution_snapshot FROM ops_reliable_task WHERE task_key=?", String.class, key));
        assertEquals("BUSINESS_APPLIED", confirmation.path("payload").path("outcome").asText());
        assertEquals("CREATED", confirmation.path("payload").path("effectKind").asText());
        assertEquals(order.get("delivery_order_no"), confirmation.path("payload").path("resultReferences").get(0).path("key").asText());
        assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, inbox.receive(message(test)).state());
        assertTrue(worker.runBatch("terminal-weight-duplicate").accepted() >= 1);
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM rec_delivery_order WHERE delivery_session_id=?", Integer.class, test.fixture.session));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM ops_reliable_task WHERE task_key=?", Integer.class, key));
        assertNoFundsOrAutomaticReview(test);
    }

    private void assertNoFundsOrAutomaticReview(Case test) {
        assertEquals(12345L, jdbc.queryForObject("SELECT available_balance_cent FROM fund_user_wallet WHERE tenant_id=? AND organization_id=?",
                Long.class, test.fixture.tenant, test.fixture.organization));
        for (String table : new String[]{"fund_user_wallet_entry", "fund_withdrawal_order", "fund_delivery_auto_withdrawal_decision"})
            assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM " + table + " WHERE tenant_id=? AND organization_id=?",
                    Integer.class, test.fixture.tenant, test.fixture.organization), table);
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM ops_reliable_task WHERE tenant_id=? AND organization_id=? AND task_type='AUTO_REVIEW_DELIVERY_ORDER'",
                Integer.class, test.fixture.tenant, test.fixture.organization));
    }

    private Case seed() throws Exception {
        ObjectNode event = sample("delivery-complete.event.json"), cloud = sample("start-delivery-session.command.json");
        String session = UUID.randomUUID().toString(), command = UUID.randomUUID().toString();
        var payload = (ObjectNode) event.path("payload");
        payload.put("sessionUid", session).put("completionReason", "TERMINAL_WEIGHT_FAILURE").putNull("deliveryNetWeightGrams");
        ((ObjectNode) payload.path("firstPreOpenMeasurement")).put("calibrationVersion", 1);
        ((ObjectNode) payload.path("finalPostCloseMeasurement")).put("calibrationVersion", 1)
                .put("status", "TIMEOUT").put("weightValueAvailable", false).putNull("reportedWeightGrams")
                .put("weightValueKind", "NONE").put("measurementElapsedMs", 5000).put("sampleCount", 0)
                .put("sensorHealth", "TIMEOUT").put("faultCode", "WEIGHT_TIMEOUT");
        event.put("eventUid", UUID.randomUUID().toString()).put("commandUid", command)
                .put("occurredAt", java.time.Instant.now().toString());
        ((ObjectNode) event.path("target")).put("uid", session);
        ((ObjectNode) cloud.path("payload")).put("sessionUid", session).put("bagUid", UUID.randomUUID().toString());
        ((ObjectNode) cloud.path("target")).put("uid", session);
        cloud.put("commandUid", command).put("payloadSha256", digest((ObjectNode) cloud.path("payload")));
        UUID[] task = new UUID[1];
        var fixture = new TransactionTemplate(transactions).execute(status -> {
            var value = new DeliveryIssueFullSchemaFixture(jdbc, event, cloud);
            jdbc.update("""
                    INSERT INTO dev_device_runtime_state(asset_id,tenant_id,organization_id,edge_connection_status,
                        mcu_link_status,safety_status,aggregate_weight_health,camera_health,local_storage_health,
                        clock_sync_health,created_at,updated_at)
                    VALUES(?,?,?,'UNKNOWN','UNKNOWN','SAFE','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN',?,?)
                    """, value.asset, value.tenant, value.organization, value.now, value.now);
            jdbc.update("""
                    INSERT INTO dev_port_runtime_state(port_id,tenant_id,organization_id,asset_id,delivery_door_state,
                        delivery_door_actuator_health,delivery_door_contact_state,clean_lock_power_state,
                        clean_solenoid_health,clean_door_inferred_state,clean_door_state_basis,weight_sensor_health,
                        infrared_value,infrared_sensor_health,smoke_state,smoke_sensor_health,safety_status,created_at,updated_at)
                    SELECT port_id,tenant_id,organization_id,asset_id,'UNKNOWN','UNKNOWN','UNAVAILABLE','DEENERGIZED',
                        'UNKNOWN','UNKNOWN','NOT_OBSERVABLE','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN','UNKNOWN','SAFE',?,?
                    FROM dev_delivery_session WHERE id=?
                    """, value.now, value.now, value.session);
            jdbc.update("INSERT INTO rec_organization_order_counter(tenant_id,organization_id,updated_at) VALUES(?,?,?)", value.tenant, value.organization, value.now);
            jdbc.update("INSERT INTO rec_bag_current_occupancy(bag_id,tenant_id,organization_id,occupancy_type,port_id,acquired_at) SELECT bag_id,tenant_id,organization_id,'PORT_BOUND',port_id,? FROM dev_delivery_session WHERE id=?", value.now, value.session);
            jdbc.update("""
                    INSERT INTO rec_port_capacity_state(port_id,tenant_id,organization_id,asset_id,current_bag_id,
                        baseline_state,latest_stable_total_weight_g,detection_gate,confirmed_fullness_state,updated_at)
                    SELECT port_id,tenant_id,organization_id,asset_id,bag_id,'INVALID',1111,'UNKNOWN','UNKNOWN',?
                    FROM dev_delivery_session WHERE id=?
                    """, value.now, value.session);
            cloud.put("targetDeviceName", value.sn);
            task[0] = registration.register(new ReliableDeviceTaskRegistration(
                    "START_DELIVERY_SESSION", "START_DELIVERY_SESSION:" + session.toUpperCase(java.util.Locale.ROOT),
                    "DELIVERY_SESSION", session, commandRefs.issue(value.tenant, value.organization, value.asset, value.command),
                    2, cloud.toString(), HexFormat.of().parseHex(digest(cloud)), UUID.fromString(session), UUID.fromString(command), 8, false, null));
            return value;
        });
        return new Case(fixture, event, task[0]);
    }

    private TrustedInboxMessage message(Case test) {
        test.event.put("payloadSha256", digest((ObjectNode) test.event.path("payload")));
        ObjectNode normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", test.fixture.sn);
        normalized.put("eventCanonicalSha256", digest(test.event)).set("event", test.event);
        String body = normalized.toString();
        UUID uid = UUID.fromString(test.event.path("eventUid").asText());
        return new TrustedInboxMessage("onenet.terminal-weight", "test-product:" + test.fixture.sn, uid.toString(),
                "DELIVERY_COMPLETE", 2, body.getBytes(StandardCharsets.UTF_8), body, "ONENET_MQ", "local-test:" + uid,
                uid, UUID.fromString(test.event.path("commandUid").asText()), TrustedInboxExecutionLane.DEVICE,
                sourceScopes.resolverForOrganizationAsset(test.fixture.sn));
    }

    @SuppressWarnings("unchecked")
    private String digest(ObjectNode node) {
        return HexFormat.of().formatHex(new DeviceConfigurationCanonicalizer()
                .payloadSha256(mapper.convertValue(node, Map.class)));
    }

    private ObjectNode sample(String name) throws Exception {
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        return (ObjectNode) mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet/" + name)));
    }

    private record Case(DeliveryIssueFullSchemaFixture fixture, ObjectNode event, UUID originalTask) {}
}
