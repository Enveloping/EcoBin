package org.enveloping.ecobin;

import org.enveloping.ecobin.operations.api.inbox.*;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.device.api.result.NativeDeliveryIssueEvidence;
import org.enveloping.ecobin.device.api.uart.EcobinUartProtocol;
import org.enveloping.ecobin.framework.reliability.DeviceCommandTaskRefFactory;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistration;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskRegistrationPort;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.TestMethodOrder;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import java.util.Map;
import java.util.HexFormat;
import java.nio.ByteBuffer;
import java.util.HashMap;
import java.sql.DriverManager;

import static org.junit.jupiter.api.Assertions.*;

/** Real runtime account, full migration history and real inbox/task transactions; no external dispatch. */
@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_ISSUE_RELIABLE_MYSQL_URL}",
        "spring.datasource.username=ecobin_app",
        "spring.datasource.password=",
        "spring.sql.init.mode=never",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "ecobin.operations.reliable.workers-enabled=false",
        "ecobin.device.activation.scheduler-enabled=false",
        "ecobin.device.delivery-observation-reconciliation.scheduler-enabled=false",
        "ecobin.identity.default-platform-admin.enabled=false",
        "ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false",
        "onenet.subscription.enabled=false",
        "bagCodeKeyK1=AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=",
        "jwt.secret=LOCAL_ISSUE_RELIABLE_TEST_SECRET_AT_LEAST_32_BYTES"
})
@EnabledIfEnvironmentVariable(named = "ECOBIN_ISSUE_RELIABLE_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_issue_p1bl")
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class DeliveryIssueReliableMysqlIntegrationTest {
    @Autowired JdbcTemplate jdbc;
    @Autowired ObjectMapper mapper;
    @Autowired TrustedInboxPort inbox;
    @Autowired ReliableDeviceInboxWorkerPort worker;
    @Autowired ReliableDeviceTaskRegistrationPort registration;
    @Autowired DeviceCommandTaskRefFactory commandRefs;
    @Autowired PlatformTransactionManager transactions;

    @ParameterizedTest @ValueSource(booleans={false,true}) @Order(1)
    void archiveFinishesOriginalTaskAndCreatesOnlyDiagnosticConfirmationAtomically(boolean failConfirmation) throws Exception {
        ObjectNode archive = sample("delivery-issue-archived.event.json");
        ObjectNode cloud = sample("start-delivery-session.command.json");
        UUID sessionUid = UUID.randomUUID(), commandUid = UUID.randomUUID(), issueUid = UUID.randomUUID();
        ObjectNode payload = (ObjectNode)archive.path("payload");
        ((ObjectNode)cloud.path("payload")).put("sessionUid", sessionUid.toString()).put("bagUid", UUID.randomUUID().toString());
        ((ObjectNode)cloud.path("target")).put("uid", sessionUid.toString());
        cloud.put("commandUid", commandUid.toString());
        cloud.put("payloadSha256", digest((ObjectNode)cloud.path("payload")));
        payload.put("sessionUid", sessionUid.toString()).put("issueUid", issueUid.toString())
                .put("originalCommandUid", commandUid.toString()).put("originalCommandPayloadSha256", cloud.path("payloadSha256").asText());
        byte[] start = HexFormat.of().parseHex(payload.path("originalStartPayloadHex").asText());
        ByteBuffer.wrap(start).position(EcobinUartProtocol.START_DELIVERY_SESSION_SESSION_UID_OFFSET)
                .putLong(sessionUid.getMostSignificantBits()).putLong(sessionUid.getLeastSignificantBits());
        byte[] domain = "ECOBIN:UART:COMMAND:v2\0".getBytes(StandardCharsets.US_ASCII);
        byte[] preimage = ByteBuffer.allocate(domain.length + 3 + start.length - 48).put(domain)
                .put((byte)EcobinUartProtocol.MESSAGE_START_DELIVERY_SESSION).putShort((short)(start.length - 48))
                .put(start, 48, start.length - 48).array();
        System.arraycopy(NativeDeliveryIssueEvidence.sha256(preimage), 0, start,
                EcobinUartProtocol.START_DELIVERY_SESSION_COMMAND_DIGEST_SHA256_OFFSET, 32);
        payload.put("originalStartPayloadHex", HexFormat.of().formatHex(start));
        archive.put("eventUid", issueUid.toString()).put("commandUid", commandUid.toString()).put("payloadSha256", digest(payload));
        ((ObjectNode)archive.path("target")).put("uid", sessionUid.toString());
        NativeDeliveryIssueEvidence.validateArchive(payload.path("originalStartPayloadHex").asText(),
                payload.path("bootObservationType").asText(), payload.path("bootObservationPayloadHex").asText(),
                sessionUid, 2, payload.path("sourceMcuBootId").asLong(), payload.path("targetMcuBootId").asLong());
        UUID[] originalTask = new UUID[1];
        var fixture = new TransactionTemplate(transactions).execute(status -> {
            var created = new DeliveryIssueFullSchemaFixture(jdbc, archive, cloud);
            cloud.put("targetDeviceName", created.sn);
            originalTask[0] = registration.register(new ReliableDeviceTaskRegistration(
                    "START_DELIVERY_SESSION", "START_DELIVERY_SESSION:" + sessionUid.toString().toUpperCase(),
                    "DELIVERY_SESSION", sessionUid.toString(), commandRefs.issue(created.tenant, created.organization, created.asset, created.command),
                    2, cloud.toString(), HexFormat.of().parseHex(digest(cloud)), sessionUid, commandUid, 8, false, null));
            return created;
        });
        assertEquals("PENDING", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?", String.class, originalTask[0].toString()));
        var receipt = inbox.receive(message(fixture.sn, archive));
        assertEquals(TrustedInboxReceiptState.ACCEPTED, receipt.state());
        if (failConfirmation) {
            // Inject only a database boundary failure in this disposable loopback database.
            String trigger = "p1bm_confirm_" + UUID.randomUUID().toString().replace("-", "");
            try (var admin = DriverManager.getConnection(System.getenv("ECOBIN_ISSUE_RELIABLE_MYSQL_URL"), "root", "");
                 var sql = admin.createStatement()) {
                assertEquals("ecobin_issue_p1bl", admin.getCatalog());
                sql.execute("CREATE TRIGGER " + trigger + " BEFORE INSERT ON ops_reliable_task FOR EACH ROW BEGIN "
                        + "IF NEW.task_type='CONFIRM_EDGE_EVENT' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='injected issue confirmation failure'; END IF; END");
                try {
                    var failed = worker.runBatch("p1bm-injected-failure");
                    assertEquals(1, failed.claimed()); assertEquals(1, failed.failed()); assertEquals(0, failed.accepted());
                    assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM dev_delivery_issue WHERE issue_uid=?", Integer.class, issueUid.toString()));
                    assertEquals("IN_PROGRESS", jdbc.queryForObject("SELECT status FROM dev_delivery_session WHERE id=?", String.class, fixture.session));
                    assertEquals("PENDING", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?", String.class, originalTask[0].toString()));
                    assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id=? AND delivery_session_id=?", Integer.class, fixture.asset, fixture.session));
                } finally { sql.execute("DROP TRIGGER " + trigger); }
            }
            assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, inbox.receive(message(fixture.sn, archive)).state());
        }
        var applied = worker.runBatch("p1bm-archive");
        assertEquals(1, applied.claimed()); assertEquals(1, applied.accepted()); assertEquals(0, applied.failed());
        assertEquals("DEVICE_ABORTED|MCU_RESTART_FINAL_RESULT_UNAVAILABLE", jdbc.queryForObject(
                "SELECT CONCAT(status,'|',end_reason) FROM dev_delivery_session WHERE id=?", String.class, fixture.session));
        assertEquals("DONE", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?", String.class, originalTask[0].toString()));
        assertEquals("PROCESSED", jdbc.queryForObject("SELECT processing_state FROM ops_inbox_message WHERE inbox_uid=?", String.class, receipt.inboxUid().toString()));
        assertEquals("DONE", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?", String.class, receipt.taskUid().toString()));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM dev_device_occupancy WHERE asset_id=?", Integer.class, fixture.asset));
        assertEquals("NONE", jdbc.queryForObject("SELECT business_value FROM dev_delivery_issue WHERE issue_uid=?", String.class, issueUid.toString()));
        assertEquals(12345L, jdbc.queryForObject("SELECT available_balance_cent FROM fund_user_wallet WHERE tenant_id=? AND organization_id=?",
                Long.class, fixture.tenant, fixture.organization));
        String key = "CONFIRM_EDGE_EVENT:" + issueUid.toString().toUpperCase();
        var confirmation = mapper.readTree(jdbc.queryForObject("SELECT redacted_execution_snapshot FROM ops_reliable_task WHERE task_key=?", String.class, key));
        assertEquals("BUSINESS_APPLIED", confirmation.path("payload").path("outcome").asText());
        assertEquals("UPDATED", confirmation.path("payload").path("effectKind").asText());
        assertEquals(0, confirmation.path("payload").path("resultReferences").size());
        for (String table : new String[]{"dev_physical_result", "rec_delivery_order", "fund_user_wallet_entry", "fund_withdrawal_order", "fund_delivery_auto_withdrawal_decision"})
            assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class), table);
        assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, inbox.receive(message(fixture.sn, archive)).state());
        assertEquals(1, worker.runBatch("p1bm-duplicate").accepted());
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM dev_delivery_issue WHERE issue_uid=?", Integer.class, issueUid.toString()));
        assertEquals(1, jdbc.queryForObject("SELECT COUNT(*) FROM ops_reliable_task WHERE task_key=?", Integer.class, key));

        var later = new TransactionTemplate(transactions).execute(status -> fixture.seedLaterDeliveryWithAnotherBag());
        byte[] lateRaw = lateResult(archive);
        ObjectNode late = fragment(archive, lateRaw, "FINAL_RESULT");
        assertEquals(TrustedInboxReceiptState.ACCEPTED, inbox.receive(message(fixture.sn, late)).state());
        assertEquals(1, worker.runBatch("p1bm-late-result").accepted());
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM dev_delivery_issue_evidence e JOIN dev_delivery_issue i ON i.id=e.issue_id
                WHERE i.issue_uid=? AND e.evidence_kind='FINAL_RESULT'
                """, Integer.class, issueUid.toString()));
        assertArrayEquals(lateRaw, jdbc.queryForObject("""
                SELECT p.part_bytes FROM dev_delivery_issue_part p JOIN dev_delivery_issue i ON i.id=p.issue_id
                WHERE i.issue_uid=? AND p.evidence_kind='FINAL_RESULT'
                """, byte[].class, issueUid.toString()));
        assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, inbox.receive(message(fixture.sn, late)).state());
        assertEquals(1, worker.runBatch("p1bm-late-duplicate").accepted());
        assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, inbox.receive(message(fixture.sn, archive)).state());
        assertEquals(1, worker.runBatch("p1bm-archive-after-later-work").accepted());
        assertEquals(later.sessionId(), jdbc.queryForObject("SELECT delivery_session_id FROM dev_device_occupancy WHERE asset_id=?", Long.class, fixture.asset));
        assertEquals(later.bagId(), jdbc.queryForObject("SELECT bag_id FROM rec_bag_current_occupancy WHERE tenant_id=? AND organization_id=?", Long.class, fixture.tenant, fixture.organization));
        assertEquals("IN_PROGRESS", jdbc.queryForObject("SELECT status FROM dev_delivery_session WHERE id=?", String.class, later.sessionId()));
        assertEquals("DEVICE_ABORTED", jdbc.queryForObject("SELECT status FROM dev_delivery_session WHERE id=?", String.class, fixture.session));
        assertEquals(12345L, jdbc.queryForObject("SELECT available_balance_cent FROM fund_user_wallet WHERE tenant_id=? AND organization_id=?", Long.class, fixture.tenant, fixture.organization));
        for (String table : new String[]{"dev_physical_result", "rec_delivery_order", "fund_user_wallet_entry", "fund_withdrawal_order", "fund_delivery_auto_withdrawal_decision"})
            assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class), table);
    }

    @Test @Order(2) void fragmentBeforeArchiveRemainsDurablyRetryableWithoutBusinessConfirmation() throws Exception {
        var countsBefore = new HashMap<String,Integer>();
        for (String table : new String[]{"dev_delivery_issue", "dev_delivery_issue_part", "dev_delivery_issue_evidence", "dev_device_occupancy"})
            countsBefore.put(table, jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class));
        assertEquals("ecobin_app@%", jdbc.queryForObject("SELECT CURRENT_USER()", String.class));
        assertEquals(84, jdbc.queryForObject("SELECT COUNT(*) FROM flyway_schema_history WHERE success=1", Integer.class));
        String sn = "P1BL-" + UUID.randomUUID();
        jdbc.update("""
                INSERT INTO dev_device_asset(asset_uid,device_public_code,hardware_sn,model_name,
                    expected_port_count,installation_display_name,installation_updated_at,created_at,updated_at)
                VALUES(?,?,?,'local issue reliable test',2,'local test',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))
                """, UUID.randomUUID().toString(), "Dv_" + UUID.randomUUID().toString().replace("-", ""), sn);
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        ObjectNode event = (ObjectNode) mapper.readTree(Files.readString(root.resolve(
                "contracts/examples/onenet/delivery-issue-evidence-appended.event.json")));
        UUID eventUid = UUID.randomUUID();
        event.put("eventUid", eventUid.toString());
        ObjectNode normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", sn);
        normalized.put("eventCanonicalSha256", digest(event));
        normalized.set("event", event);
        String body = mapper.writeValueAsString(normalized);
        var message = new TrustedInboxMessage("onenet.delivery-issue", "test-product:" + sn,
                eventUid.toString(), event.path("eventType").asText(), 2,
                body.getBytes(StandardCharsets.UTF_8), body, "ONENET_MQ", "local-test:" + eventUid,
                eventUid, UUID.fromString(event.path("commandUid").asText()), TrustedInboxExecutionLane.DEVICE);
        var receipt = inbox.receive(message);
        assertEquals(TrustedInboxReceiptState.ACCEPTED, receipt.state());
        var first = worker.runBatch("p1bl-first");
        assertEquals(1, first.claimed());
        assertEquals(1, first.failed());
        assertEquals(0, first.accepted());
        assertEquals("RETRYABLE_FAILURE", jdbc.queryForObject("""
                SELECT a.technical_result FROM ops_task_attempt a JOIN ops_reliable_task t ON t.id=a.task_id
                WHERE t.task_uid=?
                """, String.class, receipt.taskUid().toString()));
        assertEquals("inbox handler raised IllegalStateException", jdbc.queryForObject("""
                SELECT a.redacted_diagnostic FROM ops_task_attempt a JOIN ops_reliable_task t ON t.id=a.task_id
                WHERE t.task_uid=?
                """, String.class, receipt.taskUid().toString()));
        assertEquals("PENDING", jdbc.queryForObject("SELECT state FROM ops_reliable_task WHERE task_uid=?",
                String.class, receipt.taskUid().toString()));
        assertEquals(0, jdbc.queryForObject("SELECT COUNT(*) FROM ops_reliable_task WHERE task_key=?",
                Integer.class, "CONFIRM_EDGE_EVENT:" + eventUid.toString().toUpperCase()));
        for (String table : new String[]{"dev_delivery_issue", "dev_delivery_issue_part", "dev_delivery_issue_evidence",
                "dev_physical_result", "rec_delivery_order", "dev_device_occupancy",
                "fund_user_wallet_entry", "fund_withdrawal_order", "fund_delivery_auto_withdrawal_decision"}) {
            assertEquals(countsBefore.getOrDefault(table, 0), jdbc.queryForObject("SELECT COUNT(*) FROM " + table, Integer.class), table);
        }
        var duplicate = inbox.receive(message);
        assertEquals(TrustedInboxReceiptState.DUPLICATE_ACCEPTED, duplicate.state());
        assertEquals(receipt.inboxUid(), duplicate.inboxUid());
        assertEquals(receipt.taskUid(), duplicate.taskUid());
        assertEquals(2, jdbc.queryForObject("SELECT delivery_count FROM ops_inbox_message WHERE inbox_uid=?",
                Integer.class, receipt.inboxUid().toString()));
    }

    @SuppressWarnings("unchecked")
    private String digest(ObjectNode node) {
        return HexFormat.of().formatHex(NativeDeliveryIssueEvidence.sha256(
                new DeviceConfigurationCanonicalizer().canonicalBytes(mapper.convertValue(node, Map.class))));
    }

    private ObjectNode sample(String name) throws Exception {
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        return (ObjectNode)mapper.readTree(Files.readString(root.resolve("contracts/examples/onenet/" + name)));
    }

    private TrustedInboxMessage message(String sn, ObjectNode event) {
        ObjectNode normalized = mapper.createObjectNode();
        normalized.putObject("trustedSource").put("deviceName", sn);
        normalized.put("eventCanonicalSha256", digest(event)); normalized.set("event", event);
        String body = normalized.toString();
        UUID uid = UUID.fromString(event.path("eventUid").asText());
        return new TrustedInboxMessage("onenet.delivery-issue", "test-product:" + sn, uid.toString(),
                event.path("eventType").asText(), 2, body.getBytes(StandardCharsets.UTF_8), body, "ONENET_MQ",
                "local-test:" + uid, uid, UUID.fromString(event.path("commandUid").asText()), TrustedInboxExecutionLane.DEVICE);
    }

    private ObjectNode fragment(ObjectNode archive, byte[] raw, String kind) {
        ObjectNode event = archive.deepCopy();
        event.put("eventUid", UUID.randomUUID().toString()).put("eventType", "DELIVERY_ISSUE_EVIDENCE_APPENDED");
        var p = event.putObject("payload");
        for (String key : new String[]{"issueUid", "sessionUid", "originalCommandUid", "archiveEvidenceSha256"})
            p.set(key, archive.path("payload").path(key));
        p.put("businessValue", "NONE").put("evidenceKind", kind).put("evidenceIndex", 0)
                .put("evidenceSha256", HexFormat.of().formatHex(NativeDeliveryIssueEvidence.sha256(raw)))
                .put("evidenceSizeBytes", raw.length).put("partCount", 1).put("partIndex", 1)
                .put("dataHex", HexFormat.of().formatHex(raw));
        event.put("payloadSha256", digest(p));
        return event;
    }

    private byte[] lateResult(ObjectNode archive) throws Exception {
        Path root = Path.of("").toAbsolutePath();
        if (!Files.isDirectory(root.resolve("contracts"))) root = root.getParent();
        var vectors = mapper.readTree(Files.readString(root.resolve("contracts/examples/uart/golden-vectors.json"))).path("vectors");
        byte[] raw = null;
        for (var vector : vectors) if (vector.path("name").asText().equals("work_result_delivery"))
            raw = HexFormat.of().parseHex(vector.path("payloadHex").asText());
        assertNotNull(raw);
        var p = archive.path("payload");
        var original = NativeDeliveryIssueEvidence.validateArchive(p.path("originalStartPayloadHex").asText(),
                p.path("bootObservationType").asText(), p.path("bootObservationPayloadHex").asText(),
                UUID.fromString(p.path("sessionUid").asText()), 2, p.path("sourceMcuBootId").asLong(), p.path("targetMcuBootId").asLong());
        var b = ByteBuffer.wrap(raw);
        b.putLong(0, original.mcuBootId()); b.putLong(12, original.sessionUid().getMostSignificantBits());
        b.putLong(20, original.sessionUid().getLeastSignificantBits()); b.putLong(62, original.configVersion()); raw[61]=2;
        b.putLong(70, original.commandUid().getMostSignificantBits()); b.putLong(78, original.commandUid().getLeastSignificantBits());
        b.putInt(86, (int)original.commandSequence()); b.putLong(124, original.mcuBootId()); b.putLong(170, original.mcuBootId());
        byte[] domain = "ECOBIN:UART:WORK-RESULT:v2\0".getBytes(StandardCharsets.US_ASCII);
        byte[] preimage = ByteBuffer.allocate(domain.length + 3 + 167).put(domain).put((byte)64)
                .putShort((short)199).put(raw, 0, 28).put(raw, 60, 139).array();
        System.arraycopy(NativeDeliveryIssueEvidence.sha256(preimage), 0, raw, 28, 32);
        NativeDeliveryIssueEvidence.validateLateResult(raw, original);
        return raw;
    }

    @Test @Order(3) void runtimeAccountCannotRewriteOrDeleteIssueEvidence() {
        assertThrows(DataAccessException.class, () -> jdbc.update("UPDATE dev_port SET port_no=port_no WHERE 1=0"));
        for (String table : new String[]{"dev_delivery_issue", "dev_delivery_issue_part", "dev_delivery_issue_evidence"}) {
            assertThrows(DataAccessException.class, () -> jdbc.update("UPDATE " + table + " SET id=id WHERE 1=0"));
            assertThrows(DataAccessException.class, () -> jdbc.update("DELETE FROM " + table + " WHERE 1=0"));
        }
    }
}
