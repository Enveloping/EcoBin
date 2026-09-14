package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.result.TrustedCleanCommandObservation;
import org.enveloping.ecobin.device.application.delivery.ApplyDeliveryCommandObservationService;
import org.enveloping.ecobin.recycling.application.device.ApplyCleanCommandObservationService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.SimpleJdbcInsert;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** Real MySQL proof for value-free native and terminal MCU failures. */
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
        "jwt.secret=LOCAL_TERMINAL_RESULT_TEST_SECRET_AT_LEAST_32_BYTES"
})
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_ISSUE_RELIABLE_MYSQL_URL",
        matches = "jdbc:mysql://127\\.0\\.0\\.1:[0-9]+/ecobin_issue_p1bl")
class TerminalMcuResultFailureMysqlIntegrationTest {

    @Autowired JdbcTemplate jdbc;
    @Autowired ObjectMapper mapper;
    @Autowired PlatformTransactionManager transactions;
    @Autowired ApplyDeliveryCommandObservationService deliveryObservations;
    @Autowired ApplyCleanCommandObservationService cleanObservations;

    @ParameterizedTest
    @ValueSource(strings = {
            "MCU_INITIAL_WEIGHT_UNAVAILABLE",
            "MCU_WORK_CANCELLED",
            "MCU_WORK_FAILED",
            "MCU_COMMUNICATION_UNAVAILABLE"
    })
    void deliveryNativeFailureAbortsWithoutBusinessValue(String reason) {
        DeliveryIssueFullSchemaFixture fixture = seedDelivery();
        applyDelivery(fixture, reason);
        applyDelivery(fixture, reason);

        Map<String, Object> session = jdbc.queryForMap("""
                SELECT status, end_reason
                FROM dev_delivery_session WHERE id = ?
                """, fixture.session);
        assertEquals("DEVICE_ABORTED", session.get("status"));
        assertEquals(reason, session.get("end_reason"));
        assertEquals(0, count("dev_device_occupancy", fixture));
        assertEquals(0, count("rec_delivery_order", fixture));
        assertEquals(0, count("fund_user_wallet_entry", fixture));
        assertEquals(0, count("fund_withdrawal_order", fixture));
        assertEquals(12345L, jdbc.queryForObject("""
                SELECT available_balance_cent FROM fund_user_wallet
                WHERE tenant_id = ? AND organization_id = ?
                """, Long.class, fixture.tenant, fixture.organization));
    }

    @Test
    void nativeCommunicationFailureCannotDeleteAnotherDeliveryOccupancy() {
        DeliveryIssueFullSchemaFixture fixture = seedDelivery();
        jdbc.update(
                "DELETE FROM dev_device_occupancy WHERE asset_id = ?",
                fixture.asset);
        DeliveryIssueFullSchemaFixture.LaterDelivery later =
                fixture.seedLaterDeliveryWithAnotherBag();

        assertThrows(IllegalStateException.class, () -> applyDelivery(
                fixture, "MCU_COMMUNICATION_UNAVAILABLE"));

        assertEquals("IN_PROGRESS", jdbc.queryForObject("""
                SELECT status FROM dev_delivery_session WHERE id = ?
                """, String.class, fixture.session));
        assertEquals(later.sessionId(), jdbc.queryForObject("""
                SELECT delivery_session_id FROM dev_device_occupancy
                WHERE asset_id = ?
                """, Long.class, fixture.asset));
        assertEquals(0, count("rec_delivery_order", fixture));
        assertEquals(0, count("fund_user_wallet_entry", fixture));
        assertEquals(0, count("fund_withdrawal_order", fixture));
    }

    @Test
    void nativeCommunicationFailureAcceptsAnAlreadyReleasedOfflineOccupancy() {
        DeliveryIssueFullSchemaFixture fixture = seedDelivery();
        jdbc.update("""
                UPDATE dev_delivery_session
                SET offline_occupancy_released_at = ?, updated_at = ?
                WHERE id = ?
                """, fixture.now.plusSeconds(1),
                fixture.now.plusSeconds(1), fixture.session);
        jdbc.update(
                "DELETE FROM dev_device_occupancy WHERE asset_id = ?",
                fixture.asset);

        applyDelivery(fixture, "MCU_COMMUNICATION_UNAVAILABLE");

        assertEquals("DEVICE_ABORTED", jdbc.queryForObject("""
                SELECT status FROM dev_delivery_session WHERE id = ?
                """, String.class, fixture.session));
        assertEquals("MCU_COMMUNICATION_UNAVAILABLE",
                jdbc.queryForObject("""
                        SELECT end_reason FROM dev_delivery_session
                        WHERE id = ?
                        """, String.class, fixture.session));
        assertEquals(0, count("dev_device_occupancy", fixture));
        assertEquals(0, count("rec_delivery_order", fixture));
        assertEquals(0, count("fund_user_wallet_entry", fixture));
        assertEquals(0, count("fund_withdrawal_order", fixture));
    }

    @Test
    void cleanInitialWeightFailureOverridesOldAcceptedHintAsZeroAction() {
        CleanFixture fixture = seedClean();
        applyClean(fixture, "MCU_INITIAL_WEIGHT_UNAVAILABLE");
        applyClean(fixture, "MCU_INITIAL_WEIGHT_UNAVAILABLE");

        Map<String, Object> operation = jdbc.queryForMap("""
                SELECT status, end_reason, first_unlock_may_have_executed,
                       first_possible_unlock_at
                FROM rec_clean_operation WHERE id = ?
                """, fixture.operationId());
        assertEquals("PRE_UNLOCK_ENDED", operation.get("status"));
        assertEquals("MCU_INITIAL_WEIGHT_UNAVAILABLE",
                operation.get("end_reason"));
        assertEquals(0, ((Number) operation.get(
                "first_unlock_may_have_executed")).intValue());
        assertNull(operation.get("first_possible_unlock_at"));
        assertEquals(0, count("dev_device_occupancy", fixture.base()));
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_port_clean_restart_interlock
                WHERE source_clean_operation_id = ?
                """, Integer.class, fixture.operationId()));
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_bag_current_occupancy
                WHERE bag_id = ? AND occupancy_type = 'CLEAN_RESERVED'
                """, Integer.class, fixture.newBagId()));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_bag_occupancy_event
                WHERE clean_operation_id = ?
                  AND event_type = 'RESERVATION_RELEASED'
                """, Integer.class, fixture.operationId()));
        assertNoCleanBusinessValue(fixture);
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
            "MCU_WORK_CANCELLED",
            "MCU_WORK_FAILED"
    })
    void cleanTerminalResultAbortsAndKeepsOnlyManualBagInterlock(
            String reason) {
        CleanFixture fixture = seedClean();
        applyClean(fixture, reason);
        applyClean(fixture, reason);

        Map<String, Object> operation = jdbc.queryForMap("""
                SELECT status, end_reason, first_unlock_may_have_executed
                FROM rec_clean_operation WHERE id = ?
                """, fixture.operationId());
        assertEquals("ABORTED", operation.get("status"));
        assertEquals(reason, operation.get("end_reason"));
        assertEquals(1, ((Number) operation.get(
                "first_unlock_may_have_executed")).intValue());
        assertEquals(0, count("dev_device_occupancy", fixture.base()));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_port_clean_restart_interlock
                WHERE source_clean_operation_id = ?
                """, Integer.class, fixture.operationId()));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_bag_current_occupancy
                WHERE bag_id = ? AND occupancy_type = 'CLEAN_RESERVED'
                """, Integer.class, fixture.newBagId()));
        assertEquals(0, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_bag_occupancy_event
                WHERE clean_operation_id = ?
                  AND event_type = 'RESERVATION_RELEASED'
                """, Integer.class, fixture.operationId()));
        assertNoCleanBusinessValue(fixture);
    }

    @Test
    void cleanTerminalFailureConstraintRejectsContradictoryPreUnlockFacts() {
        CleanFixture fixture = seedClean();

        assertThrows(Exception.class, () -> jdbc.update("""
                UPDATE rec_clean_operation
                SET status = 'ABORTED',
                    edge_saved_confirmed = 0,
                    first_unlock_may_have_executed = 0,
                    ended_at = ?,
                    end_reason = 'MCU_WORK_FAILED'
                WHERE id = ?
                """, fixture.base().now.plusSeconds(1), fixture.operationId()));
    }

    private void applyDelivery(
            DeliveryIssueFullSchemaFixture fixture,
            String reason) {
        new TransactionTemplate(transactions).executeWithoutResult(ignored ->
                deliveryObservations.apply(
                        fixture.tenant,
                        fixture.organization,
                        fixture.asset,
                        fixture.session,
                        "START_DELIVERY_SESSION",
                        "FAILED",
                        reason,
                        fixture.now,
                        fixture.now.plusSeconds(1)));
    }

    private void applyClean(CleanFixture fixture, String reason) {
        new TransactionTemplate(transactions).executeWithoutResult(ignored ->
                cleanObservations.applyCleanCommandObservation(
                        new TrustedCleanCommandObservation(
                                fixture.base().tenant,
                                fixture.base().organization,
                                fixture.base().asset,
                                fixture.operationId(),
                                "START_CLEAN_OPERATION",
                                "FAILED",
                                reason,
                                fixture.base().now,
                                fixture.base().now.plusSeconds(1))));
    }

    private DeliveryIssueFullSchemaFixture seedDelivery() {
        return new TransactionTemplate(transactions).execute(ignored -> {
            String sessionUid = UUID.randomUUID().toString();
            String commandUid = UUID.randomUUID().toString();
            ObjectNode event = mapper.createObjectNode();
            event.putObject("payload").put("sessionUid", sessionUid);
            ObjectNode cloud = mapper.createObjectNode();
            cloud.put("commandUid", commandUid);
            ObjectNode payload = cloud.putObject("payload");
            payload.put("bagUid", UUID.randomUUID().toString());
            payload.putObject("config")
                    .put("contentSha256", "aa".repeat(32))
                    .put("mcuPayloadSha256", "bb".repeat(32));
            cloud.put("payloadSha256", "cc".repeat(32));
            return new DeliveryIssueFullSchemaFixture(jdbc, event, cloud);
        });
    }

    private CleanFixture seedClean() {
        return new TransactionTemplate(transactions).execute(ignored -> {
            DeliveryIssueFullSchemaFixture base = seedDelivery();
            Map<String, Object> source = jdbc.queryForMap("""
                    SELECT port_id, organization_user_id,
                           device_config_version_id, bag_id,
                           bag_code_snapshot
                    FROM dev_delivery_session WHERE id = ?
                    """, base.session);
            jdbc.update("DELETE FROM dev_device_occupancy WHERE asset_id = ?",
                    base.asset);
            jdbc.update("""
                    UPDATE dev_delivery_session
                    SET status = 'DEVICE_ABORTED', ended_at = ?,
                        end_reason = 'TEST_FIXTURE_REPLACED_BY_CLEAN',
                        updated_at = ?
                    WHERE id = ?
                    """, base.now, base.now, base.session);

            long cleanConfig = insert("rec_organization_clean_config",
                    "tenant_id", base.tenant,
                    "organization_id", base.organization,
                    "version_no", 1L,
                    "content_sha256", java.util.HexFormat.of()
                            .parseHex("dd".repeat(32)),
                    "operation_timeout_seconds", 1800,
                    "publication_source", "SYSTEM",
                    "published_at", base.now,
                    "created_at", base.now);
            String newBagUid = UUID.randomUUID().toString();
            String newBagCode = "terminal-new-" + UUID.randomUUID();
            long newBag = insert("rec_bag",
                    "bag_uid", newBagUid,
                    "tenant_id", base.tenant,
                    "organization_id", base.organization,
                    "bag_code", newBagCode,
                    "registered_at", base.now,
                    "created_at", base.now);
            long portId = number(source, "port_id");
            long oldBagId = number(source, "bag_id");
            long operation = insert("rec_clean_operation",
                    "operation_uid", UUID.randomUUID().toString(),
                    "tenant_id", base.tenant,
                    "organization_id", base.organization,
                    "asset_id", base.asset,
                    "port_id", portId,
                    "cleaner_organization_user_id",
                    number(source, "organization_user_id"),
                    "device_config_version_id",
                    number(source, "device_config_version_id"),
                    "clean_config_version_id", cleanConfig,
                    "clean_config_version_no", 1L,
                    "operation_timeout_seconds", 1800,
                    "old_bag_binding_state", "BOUND",
                    "old_bag_id", oldBagId,
                    "old_bag_code_snapshot", source.get("bag_code_snapshot"),
                    "old_baseline_state", "MISSING",
                    "old_baseline_id", null,
                    "old_baseline_weight_g", null,
                    "pre_unlock_weight_status", "PENDING",
                    "pre_unlock_weight_g", null,
                    "pre_unlock_weight_fault_code", null,
                    "new_bag_id", newBag,
                    "new_bag_code_snapshot", newBagCode,
                    "pending_delivery_result_session_id", null,
                    "status", "IN_PROGRESS",
                    "edge_saved_confirmed", true,
                    "first_unlock_may_have_executed", true,
                    "clean_lock_deenergized_confirmed", false,
                    "cleaner_physical_close_confirmed", false,
                    "start_authorization_expires_at", base.now.plusHours(1),
                    "edge_saved_at", base.now,
                    "first_possible_unlock_at", base.now,
                    "execution_deadline_at", base.now.plusMinutes(30),
                    "reopen_count", 0,
                    "recovery_count", 0,
                    "lock_version", 0L,
                    "created_at", base.now,
                    "updated_at", base.now);
            jdbc.update("""
                    INSERT INTO dev_device_occupancy (
                        asset_id, tenant_id, organization_id,
                        occupancy_kind, clean_operation_id,
                        acquired_at, lock_version
                    ) VALUES (?, ?, ?, 'CLEAN', ?, ?, 0)
                    """, base.asset, base.tenant, base.organization,
                    operation, base.now);
            jdbc.update("""
                    INSERT INTO rec_bag_current_occupancy (
                        bag_id, tenant_id, organization_id,
                        occupancy_type, port_id, acquired_at
                    ) VALUES (?, ?, ?, 'PORT_BOUND', ?, ?)
                    """, oldBagId, base.tenant, base.organization,
                    portId, base.now);
            jdbc.update("""
                    INSERT INTO rec_bag_current_occupancy (
                        bag_id, tenant_id, organization_id,
                        occupancy_type, clean_operation_id, acquired_at
                    ) VALUES (?, ?, ?, 'CLEAN_RESERVED', ?, ?)
                    """, newBag, base.tenant, base.organization,
                    operation, base.now);
            jdbc.update("""
                    INSERT INTO rec_bag_occupancy_event (
                        event_uid, tenant_id, organization_id,
                        bag_id, port_id, clean_operation_id,
                        event_type, occurred_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?,
                              'RESERVED_FOR_CLEAN', ?, ?)
                    """, UUID.randomUUID().toString(), base.tenant,
                    base.organization, newBag, portId, operation,
                    base.now, base.now);
            return new CleanFixture(base, operation, newBag);
        });
    }

    private long insert(String table, Object... fields) {
        Map<String, Object> values = new LinkedHashMap<>();
        for (int index = 0; index < fields.length; index += 2) {
            values.put((String) fields[index], fields[index + 1]);
        }
        return new SimpleJdbcInsert(jdbc)
                .withTableName(table)
                .usingColumns(values.keySet().toArray(String[]::new))
                .usingGeneratedKeyColumns("id")
                .executeAndReturnKey(values)
                .longValue();
    }

    private void assertNoCleanBusinessValue(CleanFixture fixture) {
        assertEquals(0, count("rec_clean_record", fixture.base()));
        assertEquals(0, count("fund_user_wallet_entry", fixture.base()));
        assertEquals(1, jdbc.queryForObject("""
                SELECT COUNT(*) FROM rec_bag_current_occupancy
                WHERE bag_id = ? AND occupancy_type = 'PORT_BOUND'
                """, Integer.class, jdbc.queryForObject("""
                        SELECT old_bag_id FROM rec_clean_operation WHERE id = ?
                        """, Long.class, fixture.operationId())));
    }

    private int count(
            String table,
            DeliveryIssueFullSchemaFixture fixture) {
        return jdbc.queryForObject(
                "SELECT COUNT(*) FROM " + table
                        + " WHERE tenant_id = ? AND organization_id = ?",
                Integer.class, fixture.tenant, fixture.organization);
    }

    private static long number(Map<String, Object> row, String key) {
        return ((Number) row.get(key)).longValue();
    }

    private record CleanFixture(
            DeliveryIssueFullSchemaFixture base,
            long operationId,
            long newBagId) {
    }
}
