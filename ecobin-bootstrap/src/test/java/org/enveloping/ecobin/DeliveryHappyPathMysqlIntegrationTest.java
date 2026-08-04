package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.application.target.DeviceConfigurationCanonicalizer;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

/**
 * MySQL-authoritative proof for the deliberately narrow normal delivery slice.
 *
 * <p>The fixture makes one deployment eligible, starts a session through the
 * miniapp HTTP boundary, submits its frozen START command through the reliable
 * command worker, then feeds one trusted OneNet DELIVERY_COMPLETE into the
 * reliable inbox worker, and closes the automatic fullness workflow through
 * the real command and inbox workers. Assertions are made against the
 * resulting database facts, not against mocks of the device/recycling
 * services.</p>
 */
@SpringBootTest(properties = {
        "spring.datasource.url=${ECOBIN_DEVICE_MYSQL_URL}",
        "spring.datasource.username=${ECOBIN_DEVICE_MYSQL_USERNAME}",
        "spring.datasource.password=${ECOBIN_DEVICE_MYSQL_PASSWORD}",
        "spring.sql.init.mode=never",
        "ecobin.database.epoch.test-bypass=false",
        "ecobin.external.mode=fake",
        "ecobin.external.fake.block-inbound=true",
        "ecobin.operations.reliable.workers-enabled=false",
        "ecobin.operations.reliable.iot-device.batch-size=1",
        "ecobin.operations.reliable.iot-device.initial-backoff=1h",
        "ecobin.operations.reliable.iot-device.maximum-backoff=1h",
        "onenet.subscription.enabled=false",
        "jwt.secret=DELIVERY_TEST_SECRET_MUST_BE_AT_LEAST_32_BYTES_LONG"
})
@AutoConfigureMockMvc
@Import(TargetDeviceMysqlIntegrationTest.ProbeConfiguration.class)
@EnabledIfEnvironmentVariable(
        named = "ECOBIN_DEVICE_MYSQL_URL",
        matches = "jdbc:mysql:.+")
class DeliveryHappyPathMysqlIntegrationTest {

    private static final String PLATFORM_PASSWORD = "PlatformPass123!";
    private static final String PRINCIPAL_PASSWORD = "PrincipalPass123!";
    private static final String COS_BASE_URL =
            "https://ecobin-contract-1250000000"
                    + ".cos.ap-guangzhou.myqcloud.com";

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private JdbcTemplate jdbc;
    @Autowired
    private PasswordEncoder passwordEncoder;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private ReliableDeviceCommandWorkerPort commandWorker;
    @Autowired
    private ReliableDeviceInboxWorkerPort inboxWorker;
    @Autowired
    private TrustedInboxPort trustedInboxPort;
    @Autowired
    private TrustedDeviceSourceScopePort sourceScopePort;
    @Autowired
    private TargetDeviceMysqlIntegrationTest.AcceptedSubmissionProbe
            submissionProbe;
    @Autowired
    private DeviceConfigurationCanonicalizer canonicalizer;

    private String run;
    private String platformLogin;

    @BeforeEach
    void seedPlatformAdministrator() {
        run = Long.toUnsignedString(System.nanoTime(), 36);
        platformLogin = "delivery-platform-" + run;
        submissionProbe.reset();
        deferPriorDeliveryFixtures();
        jdbc.update("""
                        INSERT INTO iam_platform_admin (
                            platform_admin_uid, login_name, password_hash,
                            display_name, enabled, failed_login_count,
                            locked_until, auth_version, password_changed_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'Delivery integration administrator',
                            1, 0, NULL, 0, UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                UUID.randomUUID().toString(),
                platformLogin,
                passwordEncoder.encode(PLATFORM_PASSWORD));
    }

    @AfterEach
    void disableFixturePlatformAdministrator() {
        deferCurrentDeliveryFixture();
        if (platformLogin == null) {
            return;
        }
        jdbc.update("""
                        UPDATE iam_platform_admin
                        SET enabled = 0,
                            auth_version = auth_version + 1,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE login_name = ?
                          AND enabled = 1
                        """,
                platformLogin);
    }

    @Test
    void normalDeliveryCreatesPendingOrderAndExercisesReviewWalletDelta()
            throws Exception {
        ReadyDeployment ready = prepareReadyDeployment();
        seedDeliveryBusinessFacts(ready);
        MiniappUser miniappUser = registerPhoneBoundMiniappUser(ready);

        UUID operationUid = UUID.randomUUID();
        MvcResult started = mockMvc.perform(
                        post("/api/v1/miniapp/device-deployments/"
                                + ready.deploymentCode()
                                + "/ports/2/delivery-sessions")
                                .header(
                                        "Authorization",
                                        "Bearer " + miniappUser.accessToken())
                                .header(
                                        "Idempotency-Key",
                                        operationUid.toString()))
                .andReturn();
        assertEquals(
                202,
                started.getResponse().getStatus(),
                started.getResponse().getContentAsString());
        JsonNode accepted = data(started);
        UUID sessionUid =
                UUID.fromString(accepted.path("sessionUid").asText());

        assertEquals("AUTHORIZATION_QUEUED", jdbc.queryForObject("""
                        SELECT status
                        FROM dev_delivery_session
                        WHERE session_uid = ?
                        """, String.class, sessionUid.toString()));
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_occupancy occupancy
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             occupancy.delivery_session_id
                        WHERE session_row.session_uid = ?
                          AND occupancy.occupancy_kind = 'DELIVERY'
                        """, Integer.class, sessionUid.toString()));
        assertEquals("PENDING|1", jdbc.queryForObject("""
                        SELECT CONCAT(task.state, '|',
                                      task.max_auto_attempts)
                        FROM ops_reliable_task task
                        WHERE task.task_type =
                              'START_DELIVERY_SESSION'
                          AND task.target_stable_key = ?
                        """, String.class, sessionUid.toString()));

        ReliableWorkerBatchResult submission =
                commandWorker.runBatch(
                        "delivery-start-" + sessionUid);
        assertEquals(1, submission.claimed());
        assertEquals(1, submission.accepted());
        assertEquals(0, submission.failed());
        DeviceCommandSubmission downlink =
                submissionProbe.lastSubmission();
        assertNotNull(downlink);
        assertEquals(
                "START_DELIVERY_SESSION",
                downlink.commandType());
        assertEquals(ready.hardwareSn(), downlink.hardwareSn());
        assertEquals(
                sessionUid.toString(),
                objectMapper.readTree(
                                downlink.semanticEnvelopeJson())
                        .path("target")
                        .path("uid")
                        .asText());

        PhotoStatusEvent earlyPhoto =
                trustedPhotoStatus(ready, sessionUid);
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, earlyPhoto.eventUid()));
        assertEquals(
                "AVAILABLE|0|0",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            delivery_photo_id IS NOT NULL, '|',
                            linked_at IS NOT NULL
                        )
                        FROM rec_photo_terminal_fact
                        WHERE edge_event_id = (
                            SELECT id
                            FROM dev_edge_event
                            WHERE event_uid = ?
                        )
                        """,
                        String.class,
                        earlyPhoto.eventUid()));

        DeliveryEvent deliveryEvent =
                trustedDeliveryComplete(ready, sessionUid);
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, deliveryEvent.eventUid()));

        assertEquals("BUSINESS_CONFIRMED", jdbc.queryForObject("""
                        SELECT status
                        FROM dev_delivery_session
                        WHERE session_uid = ?
                          AND ended_at IS NOT NULL
                          AND device_completed_at IS NOT NULL
                        """, String.class, sessionUid.toString()));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_occupancy occupancy
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             occupancy.delivery_session_id
                        WHERE session_row.session_uid = ?
                        """, Integer.class, sessionUid.toString()));
        assertEquals(
                "PHYSICAL_SUCCEEDED|1|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            command.physical_state, '|',
                            command.edge_accepted_at IS NOT NULL, '|',
                            command.physical_started_at IS NOT NULL, '|',
                            command.physical_ended_at IS NOT NULL
                        )
                        FROM dev_device_command command
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             command.delivery_session_id
                        WHERE session_row.session_uid = ?
                          AND command.command_type =
                              'START_DELIVERY_SESSION'
                        """,
                        String.class,
                        sessionUid.toString()));
        assertEquals(
                "DONE|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            task.state, '|',
                            task.completed_at IS NOT NULL, '|',
                            task.blocked_reason_code IS NULL
                        )
                        FROM ops_reliable_task task
                        WHERE task.task_type =
                              'START_DELIVERY_SESSION'
                          AND task.target_stable_key = ?
                        """,
                        String.class,
                        sessionUid.toString()));

        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_physical_result result_row
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             result_row.delivery_session_id
                        WHERE session_row.session_uid = ?
                          AND result_row.result_type = 'DELIVERY'
                          AND result_row.delivery_pre_measurement_status =
                              'STABLE'
                          AND result_row.delivery_post_measurement_status =
                              'STABLE'
                          AND result_row.delivery_pre_weight_g = 12000
                          AND result_row.delivery_post_weight_g = 13250
                          AND result_row.delivery_final_door_command =
                              'CLOSE'
                          AND result_row
                              .delivery_final_door_physical_state_basis =
                              'NOT_OBSERVABLE'
                        """, Integer.class, sessionUid.toString()));

        Map<String, Object> order = jdbc.queryForMap("""
                SELECT order_row.id,
                       order_row.delivery_order_no,
                       order_row.review_status,
                       order_row.raw_net_weight_g,
                       order_row.raw_business_weight_kg,
                       order_row.raw_amount_cent,
                       order_row.raw_calculation_status
                FROM rec_delivery_order order_row
                JOIN dev_delivery_session session_row
                  ON session_row.id =
                     order_row.delivery_session_id
                WHERE session_row.session_uid = ?
                """, sessionUid.toString());
        assertEquals("PENDING", order.get("review_status").toString());
        assertEquals(
                1250L,
                ((Number) order.get("raw_net_weight_g")).longValue());
        assertEquals(
                "1.25",
                order.get("raw_business_weight_kg").toString());
        assertEquals(
                56L,
                ((Number) order.get("raw_amount_cent")).longValue());
        assertEquals(
                "RELIABLE",
                order.get("raw_calculation_status").toString());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_order order_row
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             order_row.delivery_session_id
                        WHERE session_row.session_uid = ?
                        """, Integer.class, sessionUid.toString()));
        assertEquals(3, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_photo
                        WHERE delivery_order_id = ?
                          AND status = 'UPLOAD_PENDING'
                          AND missing_reason = 'CAMERA_NOT_READY'
                        """, Integer.class, order.get("id")));
        assertEquals(
                "AVAILABLE|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            object_url = ?, '|',
                            photo_uid = ?
                        )
                        FROM rec_delivery_photo
                        WHERE delivery_order_id = ?
                          AND position = 'AFTER_INNER'
                        """,
                        String.class,
                        earlyPhoto.objectUrl(),
                        earlyPhoto.photoUid(),
                        order.get("id")));
        assertEquals(
                "1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            delivery_photo_id IS NOT NULL, '|',
                            linked_at IS NOT NULL
                        )
                        FROM rec_photo_terminal_fact
                        WHERE edge_event_id = (
                            SELECT id
                            FROM dev_edge_event
                            WHERE event_uid = ?
                        )
                        """,
                        String.class,
                        earlyPhoto.eventUid()));
        assertEquals(4, jdbc.queryForObject("""
                        SELECT COUNT(DISTINCT position)
                        FROM rec_delivery_photo
                        WHERE delivery_order_id = ?
                          AND position IN (
                              'BEFORE_INNER',
                              'BEFORE_OUTER',
                              'AFTER_INNER',
                              'AFTER_OUTER'
                          )
                        """, Integer.class, order.get("id")));

        Map<String, Object> confirmation = jdbc.queryForMap("""
                SELECT task.state,
                       task.source_device_command_id,
                       CAST(task.redacted_execution_snapshot AS CHAR)
                           AS execution_snapshot
                FROM ops_reliable_task task
                WHERE task.task_key = ?
                  AND task.task_type = 'CONFIRM_EDGE_EVENT'
                """,
                "CONFIRM_EDGE_EVENT:"
                        + deliveryEvent.eventUid().toUpperCase());
        assertEquals("PENDING", confirmation.get("state").toString());
        assertEquals(
                null,
                confirmation.get("source_device_command_id"));
        JsonNode confirmationEnvelope = objectMapper.readTree(
                confirmation.get("execution_snapshot").toString());
        assertEquals(
                "BUSINESS_APPLIED",
                confirmationEnvelope.path("payload")
                        .path("outcome").asText());
        assertTrue(
                hasResultReference(
                        confirmationEnvelope,
                        "DELIVERY_ORDER",
                        order.get("delivery_order_no").toString()));

        dispatchTrustedWireEvent(
                "deliveryComplete",
                deliveryEvent.wire(),
                ready.hardwareSn());
        ReliableWorkerBatchResult duplicate =
                inboxWorker.runBatch(
                        "delivery-duplicate-" + sessionUid);
        assertEquals(1, duplicate.claimed());
        assertEquals(1, duplicate.accepted());
        assertEquals(0, duplicate.failed());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_order order_row
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             order_row.delivery_session_id
                        WHERE session_row.session_uid = ?
                        """, Integer.class, sessionUid.toString()));

        assertDeliveryOrderQueryAndReviewFlow(
                ready,
                miniappUser,
                sessionUid,
                deliveryEvent,
                ((Number) order.get("id")).longValue(),
                order.get("delivery_order_no").toString());
    }

    @Test
    void trustedCleanerCompletionCreatesEditableRecordAndAtomicBagSwap()
            throws Exception {
        ReadyDeployment ready = prepareReadyDeployment();
        seedDeliveryBusinessFacts(ready);
        MiniappUser ordinary = registerPhoneBoundMiniappUser(ready);
        MiniappUser cleaner = promoteToCleaner(ready, ordinary);

        String newBagCode = "CLEAN-BAG-" + run;
        UUID idempotencyKey = UUID.randomUUID();
        MvcResult started = mockMvc.perform(
                        post("/api/v1/miniapp/device-deployments/"
                                + ready.deploymentCode()
                                + "/ports/2/clean-operations")
                                .header(
                                        "Authorization",
                                        "Bearer " + cleaner.accessToken())
                                .header(
                                        "Idempotency-Key",
                                        idempotencyKey.toString())
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "installedBagQr",
                                                newBagCode))))
                .andReturn();
        assertEquals(
                202,
                started.getResponse().getStatus(),
                started.getResponse().getContentAsString());
        UUID operationUid = UUID.fromString(
                data(started).path("operationUid").asText());

        assertEquals(
                "PREPARED|0|0|0|0",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    status, '|',
                                    edge_saved_confirmed, '|',
                                    first_unlock_may_have_executed, '|',
                                    clean_lock_deenergized_confirmed, '|',
                                    cleaner_physical_close_confirmed
                                )
                                FROM rec_clean_operation
                                WHERE operation_uid = ?
                                """,
                        String.class,
                        operationUid.toString()));
        assertEquals(4, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_clean_photo photo
                        JOIN rec_clean_operation operation
                          ON operation.id = photo.clean_operation_id
                        WHERE operation.operation_uid = ?
                          AND photo.status = 'UPLOAD_PENDING'
                          AND photo.missing_reason = 'UPLOAD_PENDING'
                        """, Integer.class, operationUid.toString()));

        ReliableWorkerBatchResult submission = commandWorker.runBatch(
                "clean-start-" + operationUid);
        assertEquals(1, submission.claimed());
        assertEquals(1, submission.accepted());
        assertEquals(0, submission.failed());
        DeviceCommandSubmission downlink =
                submissionProbe.lastSubmission();
        assertNotNull(downlink);
        assertEquals("START_CLEAN_OPERATION", downlink.commandType());
        assertEquals(
                operationUid.toString(),
                objectMapper.readTree(downlink.semanticEnvelopeJson())
                        .path("target").path("uid").asText());

        DeliveryEvent cleanEvent = trustedCleanComplete(
                ready,
                operationUid,
                downlink,
                3);
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, cleanEvent.eventUid()));

        Map<String, Object> record = jdbc.queryForMap("""
                SELECT record.id,
                       record.clean_record_no,
                       record.record_class,
                       record.pre_unlock_weight_g,
                       record.old_baseline_weight_g,
                       record.device_removed_net_weight_status,
                       record.device_removed_net_weight_g,
                       record.recalculated_removed_net_weight_status,
                       record.recalculated_removed_net_weight_g,
                       record.final_total_weight_status,
                       record.final_total_weight_g,
                       record.effective_removed_net_weight_g,
                       record.effective_weight_source,
                       record.record_remark,
                       record.lock_version
                FROM rec_clean_record record
                JOIN rec_clean_operation operation
                  ON operation.id = record.clean_operation_id
                WHERE operation.operation_uid = ?
                """, operationUid.toString());
        assertEquals("NORMAL", record.get("record_class").toString());
        assertEquals(
                20_000L,
                ((Number) record.get("pre_unlock_weight_g")).longValue());
        assertEquals(
                10_000L,
                ((Number) record.get("old_baseline_weight_g")).longValue());
        assertEquals(
                "RELIABLE|10000|RELIABLE|10000|RELIABLE|1200",
                record.get("device_removed_net_weight_status") + "|"
                        + record.get("device_removed_net_weight_g") + "|"
                        + record.get(
                        "recalculated_removed_net_weight_status") + "|"
                        + record.get(
                        "recalculated_removed_net_weight_g") + "|"
                        + record.get("final_total_weight_status") + "|"
                        + record.get("final_total_weight_g"));
        assertEquals(
                10_000L,
                ((Number) record.get(
                        "effective_removed_net_weight_g")).longValue());
        assertEquals(
                "DEVICE_RECALCULATED",
                record.get("effective_weight_source").toString());
        assertEquals(null, record.get("record_remark"));
        assertEquals(
                1L,
                ((Number) record.get("lock_version")).longValue());

        assertEquals(
                "COMPLETED|1|1|1|1|1|CLEANER_CONFIRMED",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    operation.status, '|',
                                    operation.edge_saved_confirmed, '|',
                                    operation.first_unlock_may_have_executed,
                                    '|',
                                    operation.clean_lock_deenergized_confirmed,
                                    '|',
                                    operation.cleaner_physical_close_confirmed,
                                    '|',
                                    operation.completion_record_id IS NOT NULL,
                                    '|', operation.end_reason
                                )
                                FROM rec_clean_operation operation
                                WHERE operation.operation_uid = ?
                                """,
                        String.class,
                        operationUid.toString()));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_occupancy occupancy
                        JOIN rec_clean_operation operation
                          ON operation.id = occupancy.clean_operation_id
                        WHERE operation.operation_uid = ?
                        """, Integer.class, operationUid.toString()));
        assertEquals(
                "PHYSICAL_SUCCEEDED|DONE",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    command.physical_state, '|', task.state)
                                FROM dev_device_command command
                                JOIN rec_clean_operation operation
                                  ON operation.id =
                                     command.clean_operation_id
                                JOIN ops_reliable_task task
                                  ON task.source_device_command_id =
                                     command.id
                                 AND task.task_type =
                                     'START_CLEAN_OPERATION'
                                WHERE operation.operation_uid = ?
                                """,
                        String.class,
                        operationUid.toString()));

        assertEquals(
                newBagCode,
                jdbc.queryForObject("""
                                SELECT bag.bag_code
                                FROM rec_bag_current_occupancy occupancy
                                JOIN rec_bag bag
                                  ON bag.id = occupancy.bag_id
                                WHERE occupancy.tenant_id = ?
                                  AND occupancy.organization_id = ?
                                  AND occupancy.port_id = ?
                                  AND occupancy.occupancy_type = 'PORT_BOUND'
                                """,
                        String.class,
                        ready.tenantId(),
                        ready.organizationId(),
                        ready.portId()));
        assertEquals(
                "REMOVED_BY_CLEAN|INSTALLED_BY_CLEAN",
                jdbc.queryForObject("""
                                SELECT GROUP_CONCAT(
                                    event_type ORDER BY id SEPARATOR '|')
                                FROM rec_bag_occupancy_event
                                WHERE clean_operation_id = (
                                    SELECT id
                                    FROM rec_clean_operation
                                    WHERE operation_uid = ?
                                )
                                  AND event_type IN (
                                      'REMOVED_BY_CLEAN',
                                      'INSTALLED_BY_CLEAN'
                                  )
                                """,
                        String.class,
                        operationUid.toString()));

        Map<String, Object> detection = jdbc.queryForMap("""
                SELECT detection.id,
                       detection.detection_uid,
                       detection.trigger_type,
                       detection.status,
                       detection.baseline_state_snapshot,
                       detection.baseline_weight_g_snapshot
                FROM rec_fullness_detection detection
                WHERE detection.clean_record_id = ?
                """, record.get("id"));
        assertEquals(
                "CLEAN_COMPLETE|PENDING_INITIAL_SAMPLE|VALID|1200",
                detection.get("trigger_type") + "|"
                        + detection.get("status") + "|"
                        + detection.get("baseline_state_snapshot") + "|"
                        + detection.get("baseline_weight_g_snapshot"));
        assertEquals(
                "VALID|1200|0|0.00|PENDING|UNKNOWN",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    baseline_state, '|',
                                    current_baseline_weight_g, '|',
                                    raw_net_weight_g, '|',
                                    displayed_fullness_percent, '|',
                                    detection_gate, '|',
                                    confirmed_fullness_state
                                )
                                FROM rec_port_capacity_state
                                WHERE port_id = ?
                                """,
                        String.class,
                        ready.portId()));

        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE()
                          AND table_name = 'rec_clean_revision'
                        """, Integer.class));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM information_schema.columns
                        WHERE table_schema = DATABASE()
                          AND table_name = 'rec_clean_record'
                          AND column_name IN (
                              'review_status',
                              'review_revision_no',
                              'review_revision_id',
                              'final_recognized_net_weight_kg'
                          )
                        """, Integer.class));

        assertCleanRecordQueryAndDirectEdit(
                ready,
                cleaner,
                record.get("clean_record_no").toString(),
                operationUid,
                newBagCode);

        dispatchTrustedWireEvent(
                "cleanComplete",
                cleanEvent.wire(),
                ready.hardwareSn());
        ReliableWorkerBatchResult duplicate = inboxWorker.runBatch(
                "clean-duplicate-" + operationUid);
        assertEquals(1, duplicate.claimed());
        assertEquals(1, duplicate.accepted());
        assertEquals(0, duplicate.failed());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_clean_record record
                        JOIN rec_clean_operation operation
                          ON operation.id = record.clean_operation_id
                        WHERE operation.operation_uid = ?
                        """, Integer.class, operationUid.toString()));

        completeAndAssertNotFullDetection(
                ready,
                detection.get("detection_uid").toString(),
                4);
    }

    @Test
    void cleaningFreezesPendingDeliveryAndRecoversOldFullnessOnlyAfterNotFull()
            throws Exception {
        ReadyDeployment ready = prepareReadyDeployment();
        seedDeliveryBusinessFacts(ready);
        MiniappUser ordinary = registerPhoneBoundMiniappUser(ready);

        assertFullDetectionRequiresConfirmation(ready, ordinary, 3);
        Map<String, Object> oldFullness = jdbc.queryForMap("""
                SELECT event.id AS event_id,
                       session_row.id AS session_id
                FROM rec_port_capacity_state capacity
                JOIN rec_fullness_event event
                  ON event.id = capacity.current_fullness_event_id
                 AND event.status = 'ACTIVE'
                JOIN rec_fullness_detection detection
                  ON detection.id = event.confirmed_detection_id
                JOIN rec_delivery_order order_row
                  ON order_row.id = detection.delivery_order_id
                JOIN dev_delivery_session session_row
                  ON session_row.id = order_row.delivery_session_id
                WHERE capacity.port_id = ?
                """, ready.portId());
        long oldEventId = ((Number) oldFullness.get("event_id"))
                .longValue();
        long pendingSessionId = ((Number) oldFullness.get("session_id"))
                .longValue();
        assertEquals(1, jdbc.update("""
                        UPDATE dev_port_runtime_state
                        SET pending_delivery_result_session_id = ?,
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND deployment_id = ?
                          AND port_id = ?
                        """,
                pendingSessionId,
                ready.tenantId(),
                ready.organizationId(),
                ready.deploymentId(),
                ready.portId()));
        deferConfirmationTasks(ready.deploymentId());

        MiniappUser cleaner = promoteToCleaner(ready, ordinary);
        CleanOperationStarted clean = startCleanOperation(
                ready,
                cleaner,
                "CLEAN-RECOVERY-BAG-" + run);
        assertEquals(
                pendingSessionId,
                jdbc.queryForObject("""
                                SELECT pending_delivery_result_session_id
                                FROM rec_clean_operation
                                WHERE operation_uid = ?
                                """,
                        Long.class,
                        clean.operationUid().toString()));

        trustedCleanComplete(
                ready,
                clean.operationUid(),
                clean.downlink(),
                6);
        Map<String, Object> pendingDetection = jdbc.queryForMap("""
                SELECT detection.detection_uid,
                       capacity.detection_gate,
                       capacity.current_fullness_event_id,
                       event.status AS event_status,
                       runtime.pending_delivery_result_session_id
                FROM rec_clean_operation operation
                JOIN rec_clean_record record
                  ON record.clean_operation_id = operation.id
                JOIN rec_fullness_detection detection
                  ON detection.clean_record_id = record.id
                JOIN rec_port_capacity_state capacity
                  ON capacity.port_id = operation.port_id
                JOIN rec_fullness_event event
                  ON event.id = ?
                JOIN dev_port_runtime_state runtime
                  ON runtime.tenant_id = operation.tenant_id
                 AND runtime.organization_id = operation.organization_id
                 AND runtime.deployment_id = operation.deployment_id
                 AND runtime.port_id = operation.port_id
                WHERE operation.operation_uid = ?
                """, oldEventId, clean.operationUid().toString());
        assertEquals("PENDING", pendingDetection.get("detection_gate"));
        assertEquals(
                oldEventId,
                ((Number) pendingDetection.get(
                        "current_fullness_event_id")).longValue());
        assertEquals("ACTIVE", pendingDetection.get("event_status"));
        assertEquals(
                null,
                pendingDetection.get(
                        "pending_delivery_result_session_id"));

        completeAndAssertNotFullDetection(
                ready,
                pendingDetection.get("detection_uid").toString(),
                7);
        assertEquals(
                "RECOVERED|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    status, '|',
                                    recovered_by_detection_id IS NOT NULL
                                )
                                FROM rec_fullness_event
                                WHERE id = ?
                                """,
                        String.class,
                        oldEventId));
        deferConfirmationTasks(ready.deploymentId());
    }

    @Test
    void weightOnlyCleanWithInvalidBaselineFailsGateWithoutSampling()
            throws Exception {
        ReadyDeployment ready = prepareReadyDeployment();
        seedDeliveryBusinessFacts(ready);
        useWeightOnlyFullness(ready);
        MiniappUser ordinary = registerPhoneBoundMiniappUser(ready);
        MiniappUser cleaner = promoteToCleaner(ready, ordinary);
        CleanOperationStarted clean = startCleanOperation(
                ready,
                cleaner,
                "CLEAN-INVALID-BASELINE-BAG-" + run);

        trustedCleanComplete(
                ready,
                clean.operationUid(),
                clean.downlink(),
                3,
                false);

        Map<String, Object> detection = jdbc.queryForMap("""
                SELECT detection.id,
                       detection.status,
                       detection.final_result,
                       detection.failure_code,
                       detection.disposition,
                       detection.initial_sample_id,
                       detection.terminal_sample_id
                FROM rec_fullness_detection detection
                JOIN rec_clean_record record
                  ON record.id = detection.clean_record_id
                JOIN rec_clean_operation operation
                  ON operation.id = record.clean_operation_id
                WHERE operation.operation_uid = ?
                """, clean.operationUid().toString());
        assertEquals("FAILED", detection.get("status"));
        assertEquals("SOURCE_FAILED", detection.get("final_result"));
        assertEquals(
                "WEIGHT_BASELINE_UNAVAILABLE",
                detection.get("failure_code"));
        assertEquals("APPLIED", detection.get("disposition"));
        assertEquals(null, detection.get("initial_sample_id"));
        assertEquals(null, detection.get("terminal_sample_id"));
        assertEquals(
                "INVALID|FAILED|UNKNOWN|1|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    baseline_state, '|',
                                    detection_gate, '|',
                                    confirmed_fullness_state, '|',
                                    current_detection_id IS NULL, '|',
                                    last_detection_id = ?
                                )
                                FROM rec_port_capacity_state
                                WHERE port_id = ?
                                """,
                        String.class,
                        detection.get("id"),
                        ready.portId()));
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM dev_device_command
                        WHERE fullness_detection_id = ?
                        """,
                Integer.class,
                detection.get("id")));
    }

    private void useWeightOnlyFullness(ReadyDeployment ready) {
        assertEquals(1, jdbc.update("""
                        UPDATE dev_port_config_snapshot snapshot
                        JOIN dev_config_application application
                          ON application.config_version_id =
                             snapshot.config_version_id
                         AND application.deployment_id =
                             snapshot.deployment_id
                        SET snapshot.fullness_mode = 'WEIGHT_ONLY'
                        WHERE snapshot.tenant_id = ?
                          AND snapshot.organization_id = ?
                          AND snapshot.deployment_id = ?
                          AND snapshot.port_id = ?
                          AND application.status = 'APPLIED'
                        """,
                ready.tenantId(),
                ready.organizationId(),
                ready.deploymentId(),
                ready.portId()));
    }

    private CleanOperationStarted startCleanOperation(
            ReadyDeployment ready,
            MiniappUser cleaner,
            String newBagCode) throws Exception {
        UUID idempotencyKey = UUID.randomUUID();
        MvcResult started = mockMvc.perform(
                        post("/api/v1/miniapp/device-deployments/"
                                + ready.deploymentCode()
                                + "/ports/2/clean-operations")
                                .header(
                                        "Authorization",
                                        "Bearer " + cleaner.accessToken())
                                .header(
                                        "Idempotency-Key",
                                        idempotencyKey.toString())
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "installedBagQr",
                                                newBagCode))))
                .andReturn();
        assertEquals(
                202,
                started.getResponse().getStatus(),
                started.getResponse().getContentAsString());
        UUID operationUid = UUID.fromString(
                data(started).path("operationUid").asText());
        ReliableWorkerBatchResult submission = commandWorker.runBatch(
                "clean-start-" + operationUid);
        assertEquals(1, submission.claimed());
        assertEquals(1, submission.accepted());
        assertEquals(0, submission.failed());
        DeviceCommandSubmission downlink = submissionProbe.lastSubmission();
        assertNotNull(downlink);
        assertEquals("START_CLEAN_OPERATION", downlink.commandType());
        return new CleanOperationStarted(operationUid, downlink);
    }

    private void assertCleanRecordQueryAndDirectEdit(
            ReadyDeployment ready,
            MiniappUser cleaner,
            String cleanRecordNo,
            UUID operationUid,
            String installedBagCode) throws Exception {
        JsonNode miniappPage = miniappRead(
                cleaner,
                "/api/v1/miniapp/me/clean-records?limit=20",
                200);
        assertEquals(1, miniappPage.path("items").size());
        JsonNode miniappItem = miniappPage.path("items").get(0);
        assertEquals(
                cleanRecordNo,
                miniappItem.path("cleanRecordNo").asText());
        assertEquals(
                operationUid.toString(),
                miniappItem.path("operationUid").asText());
        assertEquals(
                cleaner.organizationUserUid().toString(),
                miniappItem.path("cleanerUserUid").asText());
        assertEquals("10.00", miniappItem.path(
                "effectiveRemovedNetWeightKg").asText());
        assertEquals("INCOMPLETE", miniappItem.path(
                "photoCompleteness").asText());

        JsonNode miniappDetail = miniappRead(
                cleaner,
                "/api/v1/miniapp/me/clean-records/" + cleanRecordNo,
                200);
        assertEquals(
                installedBagCode,
                miniappDetail.path("bags")
                        .path("installedBagQr").asText());
        assertEquals(
                "1.20",
                miniappDetail.path("newBaseline")
                        .path("baselineWeightKg").asText());
        assertTrue(miniappDetail.path("latestChange").isMissingNode());

        BrowserClient platform = new BrowserClient();
        login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        String base = "/api/v1/web/platform/tenants/"
                + ready.tenantCode()
                + "/organizations/"
                + ready.organizationCode()
                + "/clean-records";
        JsonNode webPage = data(read(
                platform,
                base + "?deploymentCode=" + ready.deploymentCode()
                        + "&portNo=2&photoCompleteness=INCOMPLETE&limit=20",
                200));
        assertEquals(1, webPage.path("items").size());
        assertEquals(
                cleanRecordNo,
                webPage.path("items").get(0)
                        .path("cleanRecordNo").asText());

        UUID setKey = UUID.randomUUID();
        Map<String, Object> setBody = Map.of(
                "expectedVersion", 1,
                "effectiveRemovedNetWeight", Map.of(
                        "action", "SET",
                        "valueKg", "9.50"),
                "recordRemark", Map.of(
                        "action", "SET",
                        "value", "现场台秤复核"),
                "reason", "设备上报重量与现场交接单不一致");
        JsonNode edited = data(write(
                platform,
                patch(base + "/" + cleanRecordNo),
                setKey,
                setBody,
                200));
        assertEquals(2, edited.path("version").asLong());
        assertEquals(
                "9.50",
                edited.path("effectiveRemovedNetWeightKg").asText());
        assertEquals(
                "MANUAL_SET",
                edited.path("effectiveWeightSource").asText());
        String firstChangeUid = edited.path("changeUid").asText();

        JsonNode replay = data(write(
                platform,
                patch(base + "/" + cleanRecordNo),
                setKey,
                setBody,
                200));
        assertEquals(firstChangeUid, replay.path("changeUid").asText());
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_clean_record_change change_row
                        JOIN rec_clean_record record
                          ON record.id = change_row.clean_record_id
                        WHERE record.clean_record_no = ?
                        """, Integer.class, cleanRecordNo));

        write(
                platform,
                patch(base + "/" + cleanRecordNo),
                setKey,
                Map.of(
                        "expectedVersion", 1,
                        "recordRemark", Map.of(
                                "action", "CLEAR"),
                        "reason", "复用键冲突"),
                409);
        write(
                platform,
                patch(base + "/" + cleanRecordNo),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 1,
                        "recordRemark", Map.of(
                                "action", "CLEAR"),
                        "reason", "过期版本"),
                409);
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_clean_record_change change_row
                        JOIN rec_clean_record record
                          ON record.id = change_row.clean_record_id
                        WHERE record.clean_record_no = ?
                        """, Integer.class, cleanRecordNo));

        JsonNode cleared = data(write(
                platform,
                patch(base + "/" + cleanRecordNo),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 2,
                        "effectiveRemovedNetWeight", Map.of(
                                "action", "CLEAR"),
                        "recordRemark", Map.of(
                                "action", "CLEAR"),
                        "reason", "撤销人工值并清空备注"),
                200));
        assertEquals(3, cleared.path("version").asLong());
        assertTrue(cleared.path(
                "effectiveRemovedNetWeightKg").isNull());
        assertEquals(
                "MANUAL_CLEARED",
                cleared.path("effectiveWeightSource").asText());

        JsonNode detail = data(read(
                platform,
                base + "/" + cleanRecordNo,
                200));
        assertEquals(3, detail.path("effective")
                .path("version").asLong());
        assertFalse(detail.path("effective")
                .path("includedInKnownWeightStatistics").asBoolean());
        assertEquals(
                "MANUAL_CLEARED",
                detail.path("effective").path("source").asText());
        assertEquals(3, detail.path("latestChange")
                .path("toVersion").asLong());

        JsonNode changes = data(read(
                platform,
                base + "/" + cleanRecordNo + "/changes?limit=1",
                200));
        assertEquals(1, changes.path("items").size());
        assertEquals(3, changes.path("items").get(0)
                .path("toVersion").asLong());
        assertFalse(changes.path("nextCursor").isNull());
        JsonNode older = data(read(
                platform,
                base + "/" + cleanRecordNo + "/changes?limit=1&cursor="
                        + changes.path("nextCursor").asText(),
                200));
        assertEquals(2, older.path("items").get(0)
                .path("toVersion").asLong());

        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_user_wallet_entry entry_row
                        WHERE entry_row.created_at >= (
                            SELECT completed_at
                            FROM rec_clean_record
                            WHERE clean_record_no = ?
                        )
                          AND entry_row.delivery_revision_id IS NULL
                        """, Integer.class, cleanRecordNo));
    }

    private void completeAndAssertNotFullDetection(
            ReadyDeployment ready,
            String detectionUid,
            long edgeEventSequence) throws Exception {
        FullnessSampleEvent sample = trustedFullnessSampleComplete(
                ready,
                detectionUid,
                "INITIAL",
                false,
                edgeEventSequence);

        assertEquals(
                "COMPLETED|NOT_FULL|APPLIED|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            final_result, '|',
                            disposition, '|',
                            initial_sample_id IS NOT NULL, '|',
                            terminal_sample_id =
                                initial_sample_id
                        )
                        FROM rec_fullness_detection
                        WHERE detection_uid = ?
                        """,
                        String.class,
                        detectionUid));
        assertEquals(
                "INITIAL|DIGITAL_INFRARED|NOT_SAMPLED|"
                        + "1|1|FIXED_FRAME_TOTAL_WEIGHT|"
                        + sample.totalWeightGrams()
                        + "|50.00|NOT_FULL",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            sample_role, '|',
                            fullness_sensor_kind, '|',
                            fullness_sample_basis, '|',
                            requested_sample_count, '|',
                            valid_sample_count, '|',
                            sample.calculation_basis, '|',
                            stable_total_weight_g, '|',
                            displayed_fullness_percent, '|',
                            conclusion
                        )
                        FROM rec_fullness_sample sample
                        JOIN rec_fullness_detection detection
                          ON detection.id = sample.detection_id
                        WHERE detection.detection_uid = ?
                          AND sample.sample_role = 'INITIAL'
                        """,
                        String.class,
                        detectionUid));
        assertEquals(
                "READY|NOT_FULL|"
                        + sample.totalWeightGrams()
                        + "|50.00|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            detection_gate, '|',
                            confirmed_fullness_state, '|',
                            latest_stable_total_weight_g, '|',
                            displayed_fullness_percent, '|',
                            current_detection_id IS NULL, '|',
                            last_detection_id IS NOT NULL
                        )
                        FROM rec_port_capacity_state
                        WHERE port_id = ?
                        """,
                        String.class,
                        ready.portId()));
        assertFullnessDeviceFactsCompleted(
                detectionUid,
                "INITIAL",
                sample);
        assertEquals(0, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_fullness_event
                        WHERE port_id = ?
                          AND status = 'ACTIVE'
                        """,
                Integer.class,
                ready.portId()));
    }

    private void assertFullDetectionRequiresConfirmation(
            ReadyDeployment ready,
            MiniappUser miniappUser,
            long deliveryEdgeEventSequence) throws Exception {
        deferConfirmationTasks(ready.deploymentId());

        UUID operationUid = UUID.randomUUID();
        MvcResult started = mockMvc.perform(
                        post("/api/v1/miniapp/device-deployments/"
                                + ready.deploymentCode()
                                + "/ports/2/delivery-sessions")
                                .header(
                                        "Authorization",
                                        "Bearer "
                                                + miniappUser.accessToken())
                                .header(
                                        "Idempotency-Key",
                                        operationUid.toString()))
                .andReturn();
        assertEquals(
                202,
                started.getResponse().getStatus(),
                started.getResponse().getContentAsString());
        UUID sessionUid = UUID.fromString(
                data(started).path("sessionUid").asText());

        ReliableWorkerBatchResult startSubmission =
                commandWorker.runBatch(
                        "delivery-full-start-" + sessionUid);
        assertEquals(1, startSubmission.claimed());
        assertEquals(1, startSubmission.accepted());
        assertEquals(0, startSubmission.failed());
        assertEquals(
                "START_DELIVERY_SESSION",
                submissionProbe.lastSubmission().commandType());

        trustedDeliveryComplete(
                ready,
                sessionUid,
                deliveryEdgeEventSequence);
        String detectionUid = jdbc.queryForObject("""
                        SELECT detection.detection_uid
                        FROM rec_fullness_detection detection
                        JOIN rec_delivery_order order_row
                          ON order_row.id =
                             detection.delivery_order_id
                        JOIN dev_delivery_session session_row
                          ON session_row.id =
                             order_row.delivery_session_id
                        WHERE session_row.session_uid = ?
                        """,
                String.class,
                sessionUid.toString());
        assertNotNull(detectionUid);

        FullnessSampleEvent initial =
                trustedFullnessSampleComplete(
                        ready,
                        detectionUid,
                        "INITIAL",
                        true,
                        deliveryEdgeEventSequence + 1);
        assertEquals(
                "WAITING_RECHECK|FULL|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            initial_sample_conclusion, '|',
                            initial_sample_id IS NOT NULL, '|',
                            next_sample_at IS NOT NULL
                        )
                        FROM rec_fullness_detection
                        WHERE detection_uid = ?
                        """,
                        String.class,
                        detectionUid));
        assertEquals(
                "IN_PROGRESS|UNKNOWN|"
                        + initial.totalWeightGrams()
                        + "|100.00|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            detection_gate, '|',
                            confirmed_fullness_state, '|',
                            latest_stable_total_weight_g, '|',
                            displayed_fullness_percent, '|',
                            current_detection_id IS NOT NULL
                        )
                        FROM rec_port_capacity_state
                        WHERE port_id = ?
                        """,
                        String.class,
                        ready.portId()));
        assertFullnessDeviceFactsCompleted(
                detectionUid,
                "INITIAL",
                initial);
        assertEquals(
                "PENDING|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            task.state, '|',
                            task.next_run_at IS NOT NULL, '|',
                            command_row.physical_state = 'QUEUED'
                        )
                        FROM ops_reliable_task task
                        JOIN dev_device_command command_row
                          ON command_row.id =
                             task.source_device_command_id
                        WHERE task.task_type = 'SAMPLE_FULLNESS'
                          AND task.target_stable_key = ?
                        """,
                        String.class,
                        detectionUid + ":CONFIRMATION"));

        FullnessSampleEvent confirmation =
                trustedFullnessSampleComplete(
                        ready,
                        detectionUid,
                        "CONFIRMATION",
                        true,
                        deliveryEdgeEventSequence + 2);
        assertEquals(
                "COMPLETED|FULL|APPLIED|FULL|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            final_result, '|',
                            disposition, '|',
                            terminal_sample_conclusion, '|',
                            initial_sample_id IS NOT NULL, '|',
                            terminal_sample_id IS NOT NULL
                        )
                        FROM rec_fullness_detection
                        WHERE detection_uid = ?
                        """,
                        String.class,
                        detectionUid));
        assertEquals(
                "READY|FULL|"
                        + confirmation.totalWeightGrams()
                        + "|100.00|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            detection_gate, '|',
                            confirmed_fullness_state, '|',
                            latest_stable_total_weight_g, '|',
                            displayed_fullness_percent, '|',
                            current_detection_id IS NULL, '|',
                            current_fullness_event_id IS NOT NULL
                        )
                        FROM rec_port_capacity_state
                        WHERE port_id = ?
                        """,
                        String.class,
                        ready.portId()));
        assertFullnessDeviceFactsCompleted(
                detectionUid,
                "CONFIRMATION",
                confirmation);
        assertEquals(
                "ACTIVE|WEIGHT|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            status, '|',
                            current_reason, '|',
                            detection_count, '|',
                            confirmed_detection_id =
                                latest_full_detection_id
                        )
                        FROM rec_fullness_event
                        WHERE port_id = ?
                          AND status = 'ACTIVE'
                        """,
                        String.class,
                        ready.portId()));
    }

    private void assertFullnessDeviceFactsCompleted(
            String detectionUid,
            String sampleRole,
            FullnessSampleEvent sample) {
        assertEquals(
                "PHYSICAL_SUCCEEDED|1|"
                        + "DIGITAL_INFRARED|NOT_SAMPLED|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            command_row.physical_state, '|',
                            EXISTS (
                                SELECT 1
                                FROM rec_fullness_sample sample
                                WHERE sample.physical_result_id =
                                      result_row.id
                            ), '|',
                            result_row.fullness_sensor_kind, '|',
                            result_row.fullness_sample_basis, '|',
                            result_row.fullness_requested_sample_count,
                            '|',
                            result_row.fullness_valid_sample_count
                        )
                        FROM dev_device_command command_row
                        JOIN dev_physical_result result_row
                          ON result_row.command_id = command_row.id
                        JOIN rec_fullness_detection detection
                          ON detection.id =
                             command_row.fullness_detection_id
                        WHERE detection.detection_uid = ?
                          AND command_row.command_uid = ?
                          AND result_row.fullness_sample_id IS NULL
                        """,
                        String.class,
                        detectionUid,
                        sample.commandUid()));
        assertEquals(
                "DONE|1|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            state, '|',
                            completed_at IS NOT NULL, '|',
                            blocked_reason_code IS NULL
                        )
                        FROM ops_reliable_task
                        WHERE task_type = 'SAMPLE_FULLNESS'
                          AND target_stable_key = ?
                        """,
                        String.class,
                        detectionUid + ":" + sampleRole));
        assertEquals(
                "PENDING|1",
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            task.state, '|',
                            task.source_device_command_id IS NULL
                        )
                        FROM ops_reliable_task task
                        WHERE task.task_key = ?
                          AND task.task_type =
                              'CONFIRM_EDGE_EVENT'
                        """,
                        String.class,
                        "CONFIRM_EDGE_EVENT:"
                                + sample.eventUid().toUpperCase()));
    }

    private void assertDeliveryOrderQueryAndReviewFlow(
            ReadyDeployment ready,
            MiniappUser owner,
            UUID sessionUid,
            DeliveryEvent deliveryEvent,
            long orderId,
            String deliveryOrderNo) throws Exception {
        BrowserClient platform = new BrowserClient();
        login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        String orderBase = "/api/v1/web/platform/tenants/"
                + ready.tenantCode()
                + "/organizations/"
                + ready.organizationCode()
                + "/delivery-orders";

        JsonNode pendingPage = data(read(
                platform,
                orderBase
                        + "?deploymentCode="
                        + ready.deploymentCode()
                        + "&portNo=2&reviewStatus=PENDING&limit=20",
                200));
        assertEquals(1, pendingPage.path("items").size());
        JsonNode pendingItem = pendingPage.path("items").get(0);
        assertEquals(
                deliveryOrderNo,
                pendingItem.path("deliveryOrderNo").asText());
        assertEquals(
                owner.organizationUserUid().toString(),
                pendingItem.path("organizationUserUid").asText());
        assertEquals(
                ready.deploymentCode(),
                pendingItem.path("deploymentCode").asText());
        assertEquals(2, pendingItem.path("portNo").asInt());
        assertEquals("1.25", pendingItem.path("rawWeightKg").asText());
        assertEquals("0.56", pendingItem.path("rawAmountYuan").asText());
        assertEquals(
                "RELIABLE",
                pendingItem.path("rawWeightReliability").asText());
        assertEquals(
                "RELIABLE",
                pendingItem.path("rawAmountReliability").asText());
        assertEquals(
                "PENDING",
                pendingItem.path("reviewStatus").asText());
        assertEquals(0, pendingItem.path("currentRevisionNo").asLong());
        assertTrue(pendingItem.path("finalWeightKg").isNull());
        assertTrue(pendingItem.path("finalAmountYuan").isNull());

        JsonNode pendingDetail = data(read(
                platform,
                orderBase + "/" + deliveryOrderNo,
                200));
        assertEquals(
                deliveryOrderNo,
                pendingDetail.path("deliveryOrderNo").asText());
        assertEquals(
                deliveryEvent.eventUid(),
                pendingDetail.path("source").path("eventUid").asText());
        assertEquals(
                sessionUid.toString(),
                pendingDetail.path("source").path("sessionUid").asText());
        assertEquals(
                ready.deploymentCode(),
                pendingDetail.path("source")
                        .path("deploymentCode").asText());
        assertEquals(
                2,
                pendingDetail.path("source").path("portNo").asInt());
        assertEquals(
                owner.organizationUserUid().toString(),
                pendingDetail.path("ownership")
                        .path("organizationUserUid").asText());
        assertEquals(
                12000,
                pendingDetail.path("raw")
                        .path("firstPreOpenWeightGram").asLong());
        assertEquals(
                13250,
                pendingDetail.path("raw")
                        .path("finalPostCloseWeightGram").asLong());
        assertEquals(
                1250,
                pendingDetail.path("raw")
                        .path("netWeightGram").asLong());
        assertEquals(
                "0.4500",
                pendingDetail.path("raw")
                        .path("unitPriceYuanPerKg").asText());
        assertEquals(
                "PENDING",
                pendingDetail.path("review").path("status").asText());
        assertEquals(0, pendingDetail.path("revisions").size());
        assertEquals(4, pendingDetail.path("photos").size());
        assertPendingWalletViews(
                platform,
                ready,
                owner,
                deliveryOrderNo);

        String orderBeforePreview = jdbc.queryForObject("""
                        SELECT CONCAT(
                            review_status, '|',
                            current_revision_no, '|',
                            COALESCE(final_business_weight_kg, 'null'), '|',
                            COALESCE(final_amount_cent, 'null'), '|',
                            DATE_FORMAT(
                                updated_at,
                                '%Y-%m-%dT%H:%i:%s.%f'
                            )
                        )
                        FROM rec_delivery_order
                        WHERE id = ?
                        """, String.class, orderId);
        int revisionsBeforePreview = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId);
        String walletBeforePreview = walletProjection(ready, owner);
        int walletEntriesBeforePreview = walletEntryCount(ready, owner);
        int auditsBeforePreview = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE target_type = 'DELIVERY_ORDER'
                          AND target_stable_key = ?
                          AND action_code IN (
                              'delivery.review',
                              'delivery.correct'
                          )
                        """, Integer.class, deliveryOrderNo);
        Map<String, Object> previewRequest = new LinkedHashMap<>();
        previewRequest.put("expectedRevisionNo", 0);
        previewRequest.put("decision", "ORIGINAL_APPROVED");
        previewRequest.put("finalWeightKg", null);
        MvcResult previewResult = write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/review-previews"),
                null,
                previewRequest,
                200);
        assertEquals(
                "no-store",
                previewResult.getResponse().getHeader("Cache-Control"));
        JsonNode preview = data(previewResult);
        assertEquals(deliveryOrderNo,
                preview.path("deliveryOrderNo").asText());
        assertEquals("INITIAL_REVIEW",
                preview.path("revisionType").asText());
        assertEquals(0, preview.path("expectedRevisionNo").asLong());
        assertEquals("ORIGINAL_APPROVED",
                preview.path("decision").asText());
        assertEquals("1.25", preview.path("finalWeightKg").asText());
        assertEquals("0.56", preview.path("finalAmountYuan").asText());
        assertEquals("0.56", preview.path("walletDeltaYuan").asText());
        assertEquals("APPLIED", preview.path("walletEffect").asText());
        assertFalse(preview.path("previewedAt").asText().isBlank());
        assertEquals(orderBeforePreview, jdbc.queryForObject("""
                        SELECT CONCAT(
                            review_status, '|',
                            current_revision_no, '|',
                            COALESCE(final_business_weight_kg, 'null'), '|',
                            COALESCE(final_amount_cent, 'null'), '|',
                            DATE_FORMAT(
                                updated_at,
                                '%Y-%m-%dT%H:%i:%s.%f'
                            )
                        )
                        FROM rec_delivery_order
                        WHERE id = ?
                        """, String.class, orderId));
        assertEquals(revisionsBeforePreview, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId));
        assertEquals(walletBeforePreview, walletProjection(ready, owner));
        assertEquals(walletEntriesBeforePreview,
                walletEntryCount(ready, owner));
        assertEquals(auditsBeforePreview, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM ops_audit_log
                        WHERE target_type = 'DELIVERY_ORDER'
                          AND target_stable_key = ?
                          AND action_code IN (
                              'delivery.review',
                              'delivery.correct'
                          )
                        """, Integer.class, deliveryOrderNo));

        Map<String, Object> initialReview = Map.of(
                "expectedRevisionNo", 0,
                "decision", "ORIGINAL_APPROVED",
                "reason", "delivery integration initial review");
        UUID firstReviewOperationUid = UUID.randomUUID();
        UUID secondReviewOperationUid = UUID.randomUUID();
        List<MvcResult> concurrentReviewResults =
                concurrentWrites(
                        platform,
                        orderBase + "/" + deliveryOrderNo + "/reviews",
                        firstReviewOperationUid,
                        secondReviewOperationUid,
                        initialReview);
        assertEquals(
                List.of(201, 409),
                concurrentReviewResults.stream()
                        .map(result -> result.getResponse().getStatus())
                        .sorted()
                        .toList());
        int successfulReviewIndex =
                concurrentReviewResults.get(0).getResponse().getStatus()
                        == 201 ? 0 : 1;
        int conflictedReviewIndex = 1 - successfulReviewIndex;
        JsonNode conflict = objectMapper.readTree(
                concurrentReviewResults.get(conflictedReviewIndex)
                        .getResponse().getContentAsByteArray());
        assertTrue(
                "DELIVERY.ORDER_ALREADY_APPROVED".equals(
                        conflict.path("code").asText())
                        || "DELIVERY.REVISION_VERSION_CONFLICT".equals(
                        conflict.path("code").asText()));
        JsonNode reviewed = data(
                concurrentReviewResults.get(successfulReviewIndex));
        UUID successfulReviewOperationUid = successfulReviewIndex == 0
                ? firstReviewOperationUid
                : secondReviewOperationUid;
        JsonNode replayed = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo + "/reviews"),
                successfulReviewOperationUid,
                initialReview,
                201));
        assertEquals(
                reviewed.path("revisionUid").asText(),
                replayed.path("revisionUid").asText());
        assertEquals(
                reviewed.path("reviewedAt").asText(),
                replayed.path("reviewedAt").asText());
        assertEquals(
                deliveryOrderNo,
                replayed.path("deliveryOrderNo").asText());
        assertEquals(
                1,
                replayed.path("revisionNo").asLong());
        assertEquals(
                "APPROVED",
                replayed.path("reviewStatus").asText());
        assertEquals(
                "APPLIED",
                replayed.path("walletEffect").asText());
        assertEquals(
                deliveryOrderNo,
                reviewed.path("deliveryOrderNo").asText());
        String initialRevisionUid =
                reviewed.path("revisionUid").asText();
        assertEquals(
                4,
                UUID.fromString(initialRevisionUid).version());
        assertEquals(1, reviewed.path("revisionNo").asLong());
        assertEquals(
                "APPROVED",
                reviewed.path("reviewStatus").asText());
        assertEquals(
                "ORIGINAL_APPROVED",
                reviewed.path("decision").asText());
        assertEquals("1.25", reviewed.path("finalWeightKg").asText());
        assertEquals("0.56", reviewed.path("finalAmountYuan").asText());
        assertEquals("0.56", reviewed.path("walletDeltaYuan").asText());
        assertEquals("APPLIED", reviewed.path("walletEffect").asText());

        assertEquals(
                "APPROVED|1|1.25|56|1",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    review_status, '|',
                                    current_revision_no, '|',
                                    final_business_weight_kg, '|',
                                    final_amount_cent, '|',
                                    first_approved_at IS NOT NULL
                                )
                                FROM rec_delivery_order
                                WHERE id = ?
                                """,
                        String.class,
                        orderId));
        assertEquals(
                "INITIAL_REVIEW|ORIGINAL_APPROVED|null|null"
                        + "|1.25|56|56",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    revision_type, '|',
                                    decision_type, '|',
                                    COALESCE(
                                        before_final_weight_kg,
                                        'null'
                                    ), '|',
                                    COALESCE(
                                        before_final_amount_cent,
                                        'null'
                                    ), '|',
                                    after_final_weight_kg, '|',
                                    after_final_amount_cent, '|',
                                    amount_delta_cent
                                )
                                FROM rec_delivery_revision
                                WHERE delivery_order_id = ?
                                  AND revision_no = 1
                                """,
                        String.class,
                        orderId));
        assertWalletState(
                ready,
                owner,
                orderId,
                56L,
                1L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56"));
        assertReviewedWalletViews(
                platform,
                ready,
                owner,
                deliveryOrderNo,
                1,
                "0.56");

        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId));
        assertWalletState(
                ready,
                owner,
                orderId,
                56L,
                1L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56"));

        Map<String, Object> correction = Map.of(
                "expectedRevisionNo", 1,
                "decision", "MODIFIED_APPROVED",
                "finalWeightKg", "2.00",
                "reason", "delivery integration correction");
        JsonNode corrected = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                correction,
                201));
        assertEquals(2, corrected.path("revisionNo").asLong());
        assertEquals(
                "MODIFIED_APPROVED",
                corrected.path("decision").asText());
        assertEquals("2.00", corrected.path("finalWeightKg").asText());
        assertEquals("0.90", corrected.path("finalAmountYuan").asText());
        assertEquals("0.34", corrected.path("walletDeltaYuan").asText());
        assertEquals("APPLIED", corrected.path("walletEffect").asText());
        assertEquals(
                "APPROVED|2|2.00|90",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    review_status, '|',
                                    current_revision_no, '|',
                                    final_business_weight_kg, '|',
                                    final_amount_cent
                                )
                                FROM rec_delivery_order
                                WHERE id = ?
                                """,
                        String.class,
                        orderId));
        assertEquals(
                "CORRECTION|MODIFIED_APPROVED|1.25|56|2.00|90|34",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    revision_type, '|',
                                    decision_type, '|',
                                    before_final_weight_kg, '|',
                                    before_final_amount_cent, '|',
                                    after_final_weight_kg, '|',
                                    after_final_amount_cent, '|',
                                    amount_delta_cent
                                )
                                FROM rec_delivery_revision
                                WHERE delivery_order_id = ?
                                  AND revision_no = 2
                                """,
                        String.class,
                        orderId));
        assertWalletState(
                ready,
                owner,
                orderId,
                90L,
                2L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56",
                        "2|DELIVERY_CORRECTION|34|56|90"));
        assertReviewedWalletViews(
                platform,
                ready,
                owner,
                deliveryOrderNo,
                2,
                "0.90");

        WithdrawalSupport withdrawalSupport =
                seedWithdrawalSupport(ready, owner);
        long preBoundaryWithdrawalId = activateWithdrawal(
                withdrawalSupport,
                "PENDING_REVIEW",
                "pre");

        JsonNode negativeCorrection = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 2,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "-1.00",
                        "reason", "negative wallet delta proof"),
                201));
        assertEquals(3,
                negativeCorrection.path("revisionNo").asLong());
        assertEquals("-0.45",
                negativeCorrection.path("finalAmountYuan").asText());
        assertEquals("-1.35",
                negativeCorrection.path("walletDeltaYuan").asText());
        assertWalletState(
                ready,
                owner,
                orderId,
                -45L,
                3L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56",
                        "2|DELIVERY_CORRECTION|34|56|90",
                        "3|DELIVERY_CORRECTION|-135|90|-45"));
        assertEquals(
                "PENDING_REVIEW|1|0|0",
                withdrawalRiskProjection(preBoundaryWithdrawalId));

        JsonNode zeroCorrection = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 3,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "0.00",
                        "reason", "return recognized amount to zero"),
                201));
        assertEquals(4, zeroCorrection.path("revisionNo").asLong());
        assertEquals("0.00",
                zeroCorrection.path("finalAmountYuan").asText());
        assertEquals("0.45",
                zeroCorrection.path("walletDeltaYuan").asText());
        assertWalletState(
                ready,
                owner,
                orderId,
                0L,
                4L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56",
                        "2|DELIVERY_CORRECTION|34|56|90",
                        "3|DELIVERY_CORRECTION|-135|90|-45",
                        "4|DELIVERY_CORRECTION|45|-45|0"));
        assertEquals(
                "PENDING_REVIEW|0|0|0",
                withdrawalRiskProjection(preBoundaryWithdrawalId));
        jdbc.update("""
                        DELETE FROM fund_active_withdrawal
                        WHERE wallet_id = ?
                        """, withdrawalSupport.walletId());
        long postBoundaryWithdrawalId = activateWithdrawal(
                withdrawalSupport,
                "CHANNEL_PROCESSING",
                "post");

        JsonNode zeroDeltaCorrection = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 4,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "0.00",
                        "reason", "zero delta still appends revision"),
                201));
        assertEquals(5,
                zeroDeltaCorrection.path("revisionNo").asLong());
        assertEquals("0.00",
                zeroDeltaCorrection.path("walletDeltaYuan").asText());
        assertEquals("NO_CHANGE",
                zeroDeltaCorrection.path("walletEffect").asText());
        assertEquals(5, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId));
        assertEquals(4, walletEntryCount(ready, owner));

        JsonNode positiveHalfUp = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 5,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "0.10",
                        "reason", "positive HALF_UP cent boundary"),
                201));
        assertEquals("0.05",
                positiveHalfUp.path("finalAmountYuan").asText());
        assertEquals("0.05",
                positiveHalfUp.path("walletDeltaYuan").asText());

        JsonNode negativeHalfUp = data(write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 6,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "-0.10",
                        "reason", "negative HALF_UP cent boundary"),
                201));
        assertEquals(7, negativeHalfUp.path("revisionNo").asLong());
        assertEquals("-0.05",
                negativeHalfUp.path("finalAmountYuan").asText());
        assertEquals("-0.10",
                negativeHalfUp.path("walletDeltaYuan").asText());
        assertWalletState(
                ready,
                owner,
                orderId,
                -5L,
                6L,
                List.of(
                        "1|DELIVERY_INITIAL_REVIEW|56|0|56",
                        "2|DELIVERY_CORRECTION|34|56|90",
                        "3|DELIVERY_CORRECTION|-135|90|-45",
                        "4|DELIVERY_CORRECTION|45|-45|0",
                        "6|DELIVERY_CORRECTION|5|0|5",
                        "7|DELIVERY_CORRECTION|-10|5|-5"));
        assertEquals(
                "CHANNEL_PROCESSING|0|1|1",
                withdrawalRiskProjection(postBoundaryWithdrawalId));

        jdbc.update("""
                        UPDATE fund_user_wallet wallet
                        JOIN iam_organization_user user_row
                          ON user_row.tenant_id = wallet.tenant_id
                         AND user_row.organization_id =
                             wallet.organization_id
                         AND user_row.id = wallet.organization_user_id
                        SET wallet.last_entry_sequence_no =
                                9007199254740991
                        WHERE wallet.tenant_id = ?
                          AND wallet.organization_id = ?
                          AND user_row.organization_user_uid = ?
                        """,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());
        String orderBeforeFundsFailure = jdbc.queryForObject("""
                        SELECT CONCAT(
                            current_revision_no, '|',
                            final_business_weight_kg, '|',
                            final_amount_cent, '|',
                            DATE_FORMAT(
                                updated_at,
                                '%Y-%m-%dT%H:%i:%s.%f'
                            )
                        )
                        FROM rec_delivery_order
                        WHERE id = ?
                        """, String.class, orderId);
        int revisionsBeforeFundsFailure = jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId);
        int entriesBeforeFundsFailure = walletEntryCount(ready, owner);
        write(
                platform,
                post(orderBase + "/" + deliveryOrderNo
                        + "/corrections"),
                UUID.randomUUID(),
                Map.of(
                        "expectedRevisionNo", 7,
                        "decision", "MODIFIED_APPROVED",
                        "finalWeightKg", "0.20",
                        "reason", "wallet sequence exhaustion rollback"),
                500);
        assertEquals(orderBeforeFundsFailure, jdbc.queryForObject("""
                        SELECT CONCAT(
                            current_revision_no, '|',
                            final_business_weight_kg, '|',
                            final_amount_cent, '|',
                            DATE_FORMAT(
                                updated_at,
                                '%Y-%m-%dT%H:%i:%s.%f'
                            )
                        )
                        FROM rec_delivery_order
                        WHERE id = ?
                        """, String.class, orderId));
        assertEquals(revisionsBeforeFundsFailure,
                jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_revision
                        WHERE delivery_order_id = ?
                        """, Integer.class, orderId));
        assertEquals(entriesBeforeFundsFailure,
                walletEntryCount(ready, owner));
        jdbc.update("""
                        UPDATE fund_user_wallet wallet
                        JOIN iam_organization_user user_row
                          ON user_row.tenant_id = wallet.tenant_id
                         AND user_row.organization_id =
                             wallet.organization_id
                         AND user_row.id = wallet.organization_user_id
                        SET wallet.last_entry_sequence_no = 6
                        WHERE wallet.tenant_id = ?
                          AND wallet.organization_id = ?
                          AND user_row.organization_user_uid = ?
                        """,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());

        JsonNode approvedDetail = data(read(
                platform,
                orderBase + "/" + deliveryOrderNo,
                200));
        assertEquals(
                "APPROVED",
                approvedDetail.path("review").path("status").asText());
        assertEquals(
                7,
                approvedDetail.path("review")
                        .path("currentRevisionNo").asLong());
        assertEquals(
                "-0.10",
                approvedDetail.path("review")
                        .path("finalWeightKg").asText());
        assertEquals(7, approvedDetail.path("revisions").size());
        assertEquals(
                "INITIAL_REVIEW",
                approvedDetail.path("revisions").get(0)
                        .path("revisionType").asText());
        assertEquals(
                "CORRECTION",
                approvedDetail.path("revisions").get(6)
                        .path("revisionType").asText());
        assertEquals(
                "-0.10",
                approvedDetail.path("revisions").get(6)
                        .path("amountDeltaYuan").asText());

        JsonNode ownerPage = miniappRead(
                owner,
                "/api/v1/miniapp/me/delivery-orders"
                        + "?reviewStatus=APPROVED",
                200);
        assertEquals(1, ownerPage.path("items").size());
        assertEquals(
                deliveryOrderNo,
                ownerPage.path("items").get(0)
                        .path("deliveryOrderNo").asText());
        assertEquals(
                "-0.10",
                ownerPage.path("items").get(0)
                        .path("finalWeightKg").asText());
        JsonNode ownerDetail = miniappRead(
                owner,
                "/api/v1/miniapp/me/delivery-orders/"
                        + deliveryOrderNo,
                200);
        assertEquals(
                deliveryOrderNo,
                ownerDetail.path("deliveryOrderNo").asText());
        assertEquals(
                "APPROVED",
                ownerDetail.path("review").path("status").asText());
        assertEquals(
                "-0.10",
                ownerDetail.path("review")
                        .path("finalWeightKg").asText());

        MiniappUser other = loginAndBindMiniappUser(
                ready,
                owner.appId(),
                "other",
                "138");
        JsonNode otherPage = miniappRead(
                other,
                "/api/v1/miniapp/me/delivery-orders",
                200);
        assertEquals(0, otherPage.path("items").size());
        miniappRead(
                other,
                "/api/v1/miniapp/me/delivery-orders/"
                        + deliveryOrderNo,
                404);
    }

    private void assertPendingWalletViews(
            BrowserClient platform,
            ReadyDeployment ready,
            MiniappUser owner,
            String deliveryOrderNo) throws Exception {
        MvcResult miniappResult = miniappReadResult(
                owner,
                "/api/v1/miniapp/me/wallet",
                200);
        assertEquals(
                "no-store",
                miniappResult.getResponse()
                        .getHeader("Cache-Control"));
        JsonNode miniappWallet = data(miniappResult);
        assertEquals(0, miniappWallet.path("walletVersion").asLong());
        assertEquals(
                "0.56",
                miniappWallet.path("pendingRewardYuan").asText());
        assertEquals(
                "0.00",
                miniappWallet.path("availableBalanceYuan").asText());
        assertEquals(
                "0.00",
                miniappWallet.path("withdrawalProcessingYuan").asText());
        assertFalse(miniappWallet.path("asOf").asText().isBlank());

        String platformWallet = platformWalletBase(ready, owner);
        MvcResult platformResult = read(
                platform,
                platformWallet,
                200);
        assertEquals(
                "no-store",
                platformResult.getResponse()
                        .getHeader("Cache-Control"));
        assertEquals(
                miniappWallet.path("pendingRewardYuan").asText(),
                data(platformResult).path("pendingRewardYuan").asText());

        JsonNode miniappEntries = data(miniappReadResult(
                owner,
                "/api/v1/miniapp/me/wallet/entries",
                200));
        assertEquals(0, miniappEntries.path("items").size());
        assertTrue(miniappEntries.path("nextCursor").isNull());

        JsonNode platformEntries = data(read(
                platform,
                platformWallet + "/entries",
                200));
        assertEquals(0, platformEntries.path("items").size());

        JsonNode organizationEntries = data(read(
                platform,
                platformOrganizationWalletEntries(ready)
                        + "?sourceNo=" + deliveryOrderNo,
                200));
        assertEquals(0, organizationEntries.path("items").size());
    }

    private void assertReviewedWalletViews(
            BrowserClient platform,
            ReadyDeployment ready,
            MiniappUser owner,
            String deliveryOrderNo,
            int expectedVersion,
            String expectedBalance) throws Exception {
        JsonNode wallet = data(miniappReadResult(
                owner,
                "/api/v1/miniapp/me/wallet",
                200));
        assertEquals(
                expectedVersion,
                wallet.path("walletVersion").asInt());
        assertEquals(
                "0.00",
                wallet.path("pendingRewardYuan").asText());
        assertEquals(
                expectedBalance,
                wallet.path("availableBalanceYuan").asText());
        assertEquals(
                "0.00",
                wallet.path("withdrawalProcessingYuan").asText());

        JsonNode personalFirst = data(miniappReadResult(
                owner,
                "/api/v1/miniapp/me/wallet/entries?limit=1",
                200));
        assertEquals(1, personalFirst.path("items").size());
        JsonNode newest = personalFirst.path("items").get(0);
        assertEquals(
                expectedVersion,
                newest.path("entrySequenceNo").asInt());
        assertEquals(
                expectedVersion == 1
                        ? "DELIVERY_INITIAL_REVIEW"
                        : "DELIVERY_CORRECTION",
                newest.path("entryType").asText());
        assertEquals(
                "DELIVERY_ORDER",
                newest.path("sourceType").asText());
        assertEquals(
                deliveryOrderNo,
                newest.path("sourceNo").asText());
        assertFalse(newest.has("organizationUserUid"));

        if (expectedVersion == 1) {
            assertTrue(personalFirst.path("nextCursor").isNull());
        } else {
            String cursor = personalFirst.path("nextCursor").asText();
            assertFalse(cursor.isBlank());
            JsonNode personalSecond = data(miniappReadResult(
                    owner,
                    "/api/v1/miniapp/me/wallet/entries"
                            + "?limit=1&cursor=" + cursor,
                    200));
            assertEquals(1, personalSecond.path("items").size());
            assertEquals(
                    1,
                    personalSecond.path("items").get(0)
                            .path("entrySequenceNo").asInt());
            assertTrue(personalSecond.path("nextCursor").isNull());
        }

        String platformWallet = platformWalletBase(ready, owner);
        JsonNode platformPersonal = data(read(
                platform,
                platformWallet + "/entries?limit=20",
                200));
        assertEquals(
                expectedVersion,
                platformPersonal.path("items").size());

        JsonNode organization = data(read(
                platform,
                platformOrganizationWalletEntries(ready)
                        + "?organizationUserUid="
                        + owner.organizationUserUid()
                        + "&sourceNo=" + deliveryOrderNo
                        + "&limit=20",
                200));
        assertEquals(
                expectedVersion,
                organization.path("items").size());
        assertEquals(
                owner.organizationUserUid().toString(),
                organization.path("items").get(0)
                        .path("organizationUserUid").asText());
        assertEquals(
                deliveryOrderNo,
                organization.path("items").get(0)
                        .path("sourceNo").asText());

        JsonNode initialOnly = data(read(
                platform,
                platformOrganizationWalletEntries(ready)
                        + "?entryType=DELIVERY_INITIAL_REVIEW",
                200));
        assertEquals(1, initialOnly.path("items").size());
        assertEquals(
                "DELIVERY_INITIAL_REVIEW",
                initialOnly.path("items").get(0)
                        .path("entryType").asText());
    }

    private static String platformWalletBase(
            ReadyDeployment ready,
            MiniappUser owner) {
        return "/api/v1/web/platform/tenants/"
                + ready.tenantCode()
                + "/organizations/"
                + ready.organizationCode()
                + "/organization-users/"
                + owner.organizationUserUid()
                + "/wallet";
    }

    private static String platformOrganizationWalletEntries(
            ReadyDeployment ready) {
        return "/api/v1/web/platform/tenants/"
                + ready.tenantCode()
                + "/organizations/"
                + ready.organizationCode()
                + "/wallet-entries";
    }

    private String walletProjection(
            ReadyDeployment ready,
            MiniappUser owner) {
        return jdbc.queryForObject("""
                        SELECT CONCAT(
                            wallet.available_balance_cent, '|',
                            wallet.frozen_withdrawal_cent, '|',
                            wallet.last_entry_sequence_no, '|',
                            wallet.delivery_gate_state, '|',
                            wallet.lock_version
                        )
                        FROM fund_user_wallet wallet
                        JOIN iam_organization_user user_row
                          ON user_row.tenant_id = wallet.tenant_id
                         AND user_row.organization_id =
                             wallet.organization_id
                         AND user_row.id = wallet.organization_user_id
                        WHERE wallet.tenant_id = ?
                          AND wallet.organization_id = ?
                          AND user_row.organization_user_uid = ?
                        """,
                String.class,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());
    }

    private int walletEntryCount(
            ReadyDeployment ready,
            MiniappUser owner) {
        return jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM fund_user_wallet_entry entry_row
                        JOIN iam_organization_user user_row
                          ON user_row.tenant_id = entry_row.tenant_id
                         AND user_row.organization_id =
                             entry_row.organization_id
                         AND user_row.id =
                             entry_row.organization_user_id
                        WHERE entry_row.tenant_id = ?
                          AND entry_row.organization_id = ?
                          AND user_row.organization_user_uid = ?
                        """,
                Integer.class,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());
    }

    private WithdrawalSupport seedWithdrawalSupport(
            ReadyDeployment ready,
            MiniappUser owner) {
        Map<String, Object> identity = jdbc.queryForMap("""
                SELECT user_row.id AS organization_user_id,
                       user_row.openid,
                       miniapp.id AS organization_miniapp_id,
                       miniapp.appid,
                       miniapp.lock_version AS miniapp_lock_version,
                       wallet.id AS wallet_id
                FROM iam_organization_user user_row
                JOIN iam_organization_miniapp miniapp
                  ON miniapp.id = user_row.organization_miniapp_id
                 AND miniapp.tenant_id = user_row.tenant_id
                 AND miniapp.organization_id = user_row.organization_id
                JOIN fund_user_wallet wallet
                  ON wallet.tenant_id = user_row.tenant_id
                 AND wallet.organization_id = user_row.organization_id
                 AND wallet.organization_user_id = user_row.id
                WHERE user_row.tenant_id = ?
                  AND user_row.organization_id = ?
                  AND user_row.organization_user_uid = ?
                """,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());
        long platformAdminId = jdbc.queryForObject("""
                        SELECT id
                        FROM iam_platform_admin
                        WHERE login_name = ?
                        """, Long.class, platformLogin);

        UUID merchantUid = UUID.randomUUID();
        String mchid = "mch" + digits(run, 20);
        jdbc.update("""
                        INSERT INTO fund_wechat_merchant_profile (
                            merchant_profile_uid, mchid,
                            merchant_kind, status,
                            scene_id, report_type, report_content,
                            transfer_page_style,
                            non_secret_config_ref,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, 'ORDINARY_MERCHANT', 'ENABLED',
                            'DELIVERY_INTEGRATION',
                            'RECYCLED_GOODS_NAME',
                            'MIXED_RECYCLABLES',
                            'STANDARD', NULL,
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """, merchantUid.toString(), mchid);
        long merchantId = jdbc.queryForObject("""
                        SELECT id
                        FROM fund_wechat_merchant_profile
                        WHERE merchant_profile_uid = ?
                        """, Long.class, merchantUid.toString());

        UUID bindingUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO fund_miniapp_merchant_binding (
                            binding_uid,
                            tenant_id, organization_id,
                            organization_miniapp_id, appid,
                            miniapp_lock_version_snapshot,
                            merchant_profile_id,
                            status, verified_by_platform_admin_id,
                            verified_at, disabled_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?,
                            'VERIFIED', ?,
                            UTC_TIMESTAMP(3), NULL,
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                bindingUid.toString(),
                ready.tenantId(),
                ready.organizationId(),
                identity.get("organization_miniapp_id"),
                identity.get("appid"),
                identity.get("miniapp_lock_version"),
                merchantId,
                platformAdminId);
        long bindingId = jdbc.queryForObject("""
                        SELECT id
                        FROM fund_miniapp_merchant_binding
                        WHERE binding_uid = ?
                        """, Long.class, bindingUid.toString());

        jdbc.update("""
                        INSERT INTO fund_organization_withdraw_config (
                            tenant_id, organization_id,
                            version_no, content_sha256,
                            hard_limit_cent,
                            manual_min_cent, manual_max_cent,
                            manual_review_free_threshold_cent,
                            publication_source,
                            published_by_staff_account_id,
                            published_at, created_at
                        ) VALUES (
                            ?, ?, 1, ?,
                            20000, 10, 20000, 0,
                            'SYSTEM', NULL,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                ready.tenantId(),
                ready.organizationId(),
                sha256(("withdraw-config-" + run)
                        .getBytes(StandardCharsets.UTF_8)));
        long withdrawConfigId = jdbc.queryForObject("""
                        SELECT id
                        FROM fund_organization_withdraw_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND version_no = 1
                        """,
                Long.class,
                ready.tenantId(),
                ready.organizationId());

        UUID accountUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO fund_organization_payout_account (
                            account_uid,
                            tenant_id, organization_id,
                            available_payout_cent,
                            frozen_withdrawal_cent,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?,
                            0, 0,
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                accountUid.toString(),
                ready.tenantId(),
                ready.organizationId());
        long payoutAccountId = jdbc.queryForObject("""
                        SELECT id
                        FROM fund_organization_payout_account
                        WHERE account_uid = ?
                        """, Long.class, accountUid.toString());

        return new WithdrawalSupport(
                ready.tenantId(),
                ready.organizationId(),
                ((Number) identity.get("organization_user_id"))
                        .longValue(),
                ((Number) identity.get("wallet_id")).longValue(),
                payoutAccountId,
                withdrawConfigId,
                bindingId,
                ((Number) identity.get("organization_miniapp_id"))
                        .longValue(),
                merchantId,
                mchid,
                identity.get("appid").toString(),
                identity.get("openid").toString());
    }

    private long activateWithdrawal(
            WithdrawalSupport support,
            String businessState,
            String suffix) {
        String withdrawalOrderNo = "WD" + suffix + run;
        jdbc.update("""
                        INSERT INTO fund_withdrawal_order (
                            withdrawal_order_no,
                            tenant_id, organization_id,
                            organization_user_id, wallet_id,
                            organization_payout_account_id,
                            withdraw_config_id,
                            withdraw_config_version_no,
                            hard_limit_cent_snapshot,
                            manual_min_cent_snapshot,
                            manual_max_cent_snapshot,
                            manual_review_free_threshold_cent_snapshot,
                            amount_cent,
                            miniapp_merchant_binding_id,
                            organization_miniapp_id,
                            merchant_profile_id,
                            mchid_snapshot, appid_snapshot,
                            openid_snapshot,
                            business_state,
                            negative_balance_pause,
                            post_boundary_risk,
                            pre_channel_block_reason,
                            channel_boundary_at,
                            long_unsettled_at, reviewed_at,
                            channel_terminal_at, ended_at,
                            lock_version, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?,
                            ?, 1,
                            20000, 10, 20000, 0,
                            10,
                            ?, ?, ?, ?, ?, ?,
                            ?,
                            0, 0, NULL,
                            CASE WHEN ? = 'CHANNEL_PROCESSING'
                                 THEN UTC_TIMESTAMP(3)
                                 ELSE NULL END,
                            NULL, NULL, NULL, NULL,
                            0, UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                withdrawalOrderNo,
                support.tenantId(),
                support.organizationId(),
                support.organizationUserId(),
                support.walletId(),
                support.payoutAccountId(),
                support.withdrawConfigId(),
                support.bindingId(),
                support.organizationMiniappId(),
                support.merchantId(),
                support.mchid(),
                support.appid(),
                support.openid(),
                businessState,
                businessState);
        long withdrawalOrderId = jdbc.queryForObject("""
                        SELECT id
                        FROM fund_withdrawal_order
                        WHERE withdrawal_order_no = ?
                        """, Long.class, withdrawalOrderNo);
        jdbc.update("""
                        INSERT INTO fund_active_withdrawal (
                            wallet_id, tenant_id, organization_id,
                            withdrawal_order_id, acquired_at
                        ) VALUES (?, ?, ?, ?, UTC_TIMESTAMP(3))
                        """,
                support.walletId(),
                support.tenantId(),
                support.organizationId(),
                withdrawalOrderId);
        return withdrawalOrderId;
    }

    private String withdrawalRiskProjection(long withdrawalOrderId) {
        return jdbc.queryForObject("""
                        SELECT CONCAT(
                            business_state, '|',
                            negative_balance_pause, '|',
                            post_boundary_risk, '|',
                            channel_boundary_at IS NOT NULL
                        )
                        FROM fund_withdrawal_order
                        WHERE id = ?
                        """, String.class, withdrawalOrderId);
    }

    private void assertWalletState(
            ReadyDeployment ready,
            MiniappUser owner,
            long orderId,
            long expectedBalanceCent,
            long expectedSequence,
            List<String> expectedEntries) {
        Map<String, Object> wallet = jdbc.queryForMap("""
                SELECT wallet.id,
                       wallet.available_balance_cent,
                       wallet.frozen_withdrawal_cent,
                       wallet.last_entry_sequence_no,
                       wallet.delivery_gate_state
                FROM fund_user_wallet wallet
                JOIN iam_organization_user user_row
                  ON user_row.tenant_id = wallet.tenant_id
                 AND user_row.organization_id =
                     wallet.organization_id
                 AND user_row.id = wallet.organization_user_id
                WHERE wallet.tenant_id = ?
                  AND wallet.organization_id = ?
                  AND user_row.organization_user_uid = ?
                """,
                ready.tenantId(),
                ready.organizationId(),
                owner.organizationUserUid().toString());
        assertEquals(
                expectedBalanceCent,
                ((Number) wallet.get("available_balance_cent"))
                        .longValue());
        assertEquals(
                0L,
                ((Number) wallet.get("frozen_withdrawal_cent"))
                        .longValue());
        assertEquals(
                expectedSequence,
                ((Number) wallet.get("last_entry_sequence_no"))
                        .longValue());
        assertEquals("OPEN", wallet.get("delivery_gate_state").toString());

        List<String> entries = jdbc.query("""
                        SELECT CONCAT(
                            revision.revision_no, '|',
                            entry_row.event_type, '|',
                            entry_row.available_delta_cent, '|',
                            entry_row.available_before_cent, '|',
                            entry_row.available_after_cent
                        ) AS entry_summary
                        FROM fund_user_wallet_entry entry_row
                        JOIN rec_delivery_revision revision
                          ON revision.id =
                             entry_row.delivery_revision_id
                        WHERE revision.delivery_order_id = ?
                          AND entry_row.wallet_id = ?
                        ORDER BY entry_row.entry_sequence_no
                        """,
                (rs, ignored) -> rs.getString("entry_summary"),
                orderId,
                wallet.get("id"));
        assertEquals(expectedEntries, entries);
        assertEquals(
                expectedSequence,
                jdbc.queryForObject("""
                                SELECT last_visibility_sequence_no
                                FROM
                                    fund_organization_wallet_entry_counter
                                WHERE tenant_id = ?
                                  AND organization_id = ?
                                """,
                        Long.class,
                        ready.tenantId(),
                        ready.organizationId()));
    }

    private ReadyDeployment prepareReadyDeployment() throws Exception {
        BrowserClient platform = new BrowserClient();
        login(
                platform,
                "/api/v1/web/platform/auth/sessions",
                platformLogin,
                PLATFORM_PASSWORD,
                201);
        String tenantCode = code("tenant");
        String organizationCode = code("org");
        String principalLogin = "delivery-principal-" + run;
        createEnabledScope(
                platform,
                tenantCode,
                organizationCode,
                principalLogin);
        BrowserClient principal = new BrowserClient();
        login(
                principal,
                "/api/v1/web/auth/sessions",
                principalLogin,
                PRINCIPAL_PASSWORD,
                201);

        String hardwareSn = "HW-DELIVERY-" + run;
        JsonNode asset = data(write(
                platform,
                post("/api/v1/web/platform/device-assets"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "modelCode", "EC-M0",
                        "productionBatch", "BATCH-" + run,
                        "expectedPortCount", 2),
                201));
        JsonNode allocation = data(write(
                platform,
                post("/api/v1/web/platform/tenants/" + tenantCode
                        + "/device-asset-allocations"),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "expectedAssetVersion",
                        asset.path("version").asLong(),
                        "reason",
                        "delivery integration allocation"),
                201));
        String organizationDeploymentBase =
                "/api/v1/web/organizations/" + organizationCode
                        + "/device-deployments";
        String deploymentBase = "/api/v1/web/platform/tenants/"
                + tenantCode + "/organizations/" + organizationCode
                + "/device-deployments";
        JsonNode deployment = data(write(
                principal,
                post(organizationDeploymentBase),
                UUID.randomUUID(),
                Map.of(
                        "allocationUid",
                        allocation.path("allocationUid").asText(),
                        "expectedAllocationVersion",
                        allocation.path("allocationVersion").asLong()),
                201));
        String deploymentCode =
                deployment.path("deploymentCode").asText();
        String applicationUid = jdbc.queryForObject("""
                        SELECT application.application_uid
                        FROM dev_config_application application
                        JOIN dev_device_deployment deployment
                          ON deployment.id = application.deployment_id
                        JOIN dev_config_version version
                          ON version.id =
                             application.config_version_id
                        WHERE deployment.public_code = ?
                          AND version.version_no = 1
                        """,
                String.class,
                deploymentCode);
        assertNotNull(applicationUid);

        acceptTransportLifecycle(
                hardwareSn,
                "ONLINE",
                Instant.now().toEpochMilli(),
                "delivery-configuration-online-" + run);
        ReliableWorkerBatchResult configurationSubmission =
                commandWorker.runBatch(
                        "delivery-configuration-" + run);
        assertEquals(1, configurationSubmission.claimed());
        assertEquals(1, configurationSubmission.accepted());
        assertEquals(0, configurationSubmission.failed());
        applyTrustedConfigurationProgress(
                hardwareSn,
                deploymentCode,
                applicationUid);
        applyTrustedRuntimeSnapshot(
                hardwareSn,
                deploymentCode,
                applicationUid,
                2);
        Long deploymentId = jdbc.queryForObject("""
                SELECT id
                FROM dev_device_deployment
                WHERE public_code = ?
                """, Long.class, deploymentCode);
        assertNotNull(deploymentId);
        deferConfirmationTasks(deploymentId);
        data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/acceptances"),
                UUID.randomUUID(),
                Map.of(
                        "expectedDeploymentVersion", 0,
                        "expectedConfigurationVersion", 1,
                        "deliveryDoorObservedNormal", true,
                        "camerasObservedNormal", true,
                        "cleanDoorInstallationObservedNormal", true,
                        "reason",
                        "trusted Orange Pi delivery fixture"),
                200));
        data(write(
                principal,
                post(organizationDeploymentBase + "/" + deploymentCode
                        + "/business-switch/enablements"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 1,
                        "reason",
                        "enable delivery happy-path fixture"),
                200));

        Map<String, Object> ids = jdbc.queryForMap("""
                SELECT deployment.tenant_id,
                       deployment.organization_id,
                       deployment.id AS deployment_id,
                       port.id AS port_id
                FROM dev_device_deployment deployment
                JOIN dev_port port
                  ON port.deployment_id = deployment.id
                 AND port.port_no = 2
                WHERE deployment.public_code = ?
                """, deploymentCode);
        return new ReadyDeployment(
                tenantCode,
                organizationCode,
                hardwareSn,
                deploymentCode,
                ((Number) ids.get("tenant_id")).longValue(),
                ((Number) ids.get("organization_id")).longValue(),
                deploymentId,
                ((Number) ids.get("port_id")).longValue());
    }

    private void seedDeliveryBusinessFacts(ReadyDeployment ready) {
        assertEquals(
                "ALL_MANUAL|-1000|100000|SYSTEM",
                jdbc.queryForObject("""
                                SELECT CONCAT(
                                    config.review_mode, '|',
                                    config.open_balance_floor_cent, '|',
                                    config.max_review_abs_weight_g, '|',
                                    config.publication_source
                                )
                                FROM rec_organization_delivery_config_head head
                                JOIN rec_organization_delivery_config config
                                  ON config.id = head.current_config_id
                                 AND config.tenant_id = head.tenant_id
                                 AND config.organization_id =
                                     head.organization_id
                                WHERE head.tenant_id = ?
                                  AND head.organization_id = ?
                                """,
                        String.class,
                        ready.tenantId(),
                        ready.organizationId()));
        assertEquals(
                1,
                jdbc.queryForObject("""
                                SELECT COUNT(*)
                                FROM rec_organization_order_counter
                                WHERE tenant_id = ?
                                  AND organization_id = ?
                                """,
                        Integer.class,
                        ready.tenantId(),
                        ready.organizationId()));

        UUID bagUid = UUID.randomUUID();
        String bagCode = "BAG-" + run;
        jdbc.update("""
                        INSERT INTO rec_bag (
                            bag_uid, tenant_id, organization_id,
                            bag_code, registered_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                bagUid.toString(),
                ready.tenantId(),
                ready.organizationId(),
                bagCode);
        long bagId = jdbc.queryForObject("""
                        SELECT id FROM rec_bag
                        WHERE bag_uid = ?
                        """, Long.class, bagUid.toString());
        jdbc.update("""
                        INSERT INTO rec_bag_current_occupancy (
                            bag_id, tenant_id, organization_id,
                            occupancy_type, port_id,
                            clean_operation_id, acquired_at
                        ) VALUES (
                            ?, ?, ?, 'PORT_BOUND', ?,
                            NULL, UTC_TIMESTAMP(3)
                        )
                        """,
                bagId,
                ready.tenantId(),
                ready.organizationId(),
                ready.portId());
        UUID bagEventUid = UUID.randomUUID();
        jdbc.update("""
                        INSERT INTO rec_bag_occupancy_event (
                            event_uid, tenant_id, organization_id,
                            bag_id, port_id, clean_operation_id,
                            event_type, occurred_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, NULL,
                            'INITIAL_INSTALLED',
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                bagEventUid.toString(),
                ready.tenantId(),
                ready.organizationId(),
                bagId,
                ready.portId());
        long bagEventId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_bag_occupancy_event
                        WHERE event_uid = ?
                        """, Long.class, bagEventUid.toString());
        jdbc.update("""
                        INSERT INTO rec_port_weight_baseline (
                            tenant_id, organization_id,
                            port_id, bag_id, version_no,
                            source_type, source_bag_event_id,
                            source_physical_result_id,
                            source_clean_record_id,
                            source_measurement_id,
                            baseline_weight_g,
                            established_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, 1,
                            'INITIAL_BINDING', ?,
                            NULL, NULL, NULL,
                            10000,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                ready.tenantId(),
                ready.organizationId(),
                ready.portId(),
                bagId,
                bagEventId);
        long baselineId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_port_weight_baseline
                        WHERE port_id = ?
                          AND version_no = 1
                        """, Long.class, ready.portId());
        byte[] fullnessRule =
                sha256(("fullness-rule-" + run)
                        .getBytes(StandardCharsets.UTF_8));
        jdbc.update("""
                        INSERT INTO rec_port_capacity_state (
                            port_id, tenant_id, organization_id,
                            deployment_id,
                            baseline_state,
                            current_baseline_id,
                            current_baseline_weight_g,
                            latest_stable_total_weight_g,
                            raw_net_weight_g,
                            displayed_fullness_percent,
                            detection_gate,
                            current_detection_id,
                            current_rule_fingerprint,
                            confirmed_fullness_state,
                            last_detection_id,
                            current_fullness_event_id,
                            lock_version, updated_at
                        ) VALUES (
                            ?, ?, ?, ?,
                            'VALID', ?, 10000,
                            12000, 2000, 4.00,
                            'READY', NULL, ?,
                            'NOT_FULL',
                            NULL, NULL,
                            0, UTC_TIMESTAMP(3)
                        )
                        """,
                ready.portId(),
                ready.tenantId(),
                ready.organizationId(),
                ready.deploymentId(),
                baselineId,
                fullnessRule);
    }

    private MiniappUser registerPhoneBoundMiniappUser(
            ReadyDeployment ready) throws Exception {
        String appId = "wx" + UUID.randomUUID().toString()
                .replace("-", "").substring(0, 16);
        jdbc.update("""
                        INSERT INTO iam_organization_miniapp (
                            tenant_id, organization_id, appid,
                            display_name, login_enabled, app_secret,
                            activated_at, lock_version,
                            configured_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'Delivery miniapp',
                            1, 'test-app-secret',
                            UTC_TIMESTAMP(3), 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3),
                            UTC_TIMESTAMP(3)
                        )
                        """,
                ready.tenantId(),
                ready.organizationId(),
                appId);
        return loginAndBindMiniappUser(
                ready,
                appId,
                "owner",
                "139");
    }

    private MiniappUser loginAndBindMiniappUser(
            ReadyDeployment ready,
            String appId,
            String identitySuffix,
            String phonePrefix) throws Exception {
        MvcResult login = mockMvc.perform(
                        post("/api/v1/miniapp/auth/sessions")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "appId", appId,
                                                "wxLoginCode",
                                                "fake:delivery:" + run
                                                        + ":"
                                                        + identitySuffix,
                                                "registrationSource",
                                                Map.of(
                                                        "deploymentCode",
                                                        ready.deploymentCode())))))
                .andReturn();
        assertEquals(
                201,
                login.getResponse().getStatus(),
                login.getResponse().getContentAsString());
        JsonNode loginData = data(login);
        String accessToken =
                loginData.path("accessToken").asText();
        UUID userUid = UUID.fromString(
                loginData.path("subjectUid").asText());

        MvcResult phone = mockMvc.perform(
                        post("/api/v1/miniapp/me/phone-bindings")
                                .header(
                                        "Authorization",
                                        "Bearer " + accessToken)
                                .header(
                                        "Idempotency-Key",
                                        UUID.randomUUID().toString())
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "wechatPhoneCode",
                                                "fake-phone:+86"
                                                        + phonePrefix
                                                        + digits(
                                                        run
                                                                + identitySuffix,
                                                        8)))))
                .andReturn();
        assertEquals(
                201,
                phone.getResponse().getStatus(),
                phone.getResponse().getContentAsString());
        return new MiniappUser(accessToken, userUid, appId);
    }

    private MiniappUser promoteToCleaner(
            ReadyDeployment ready,
            MiniappUser ordinary) throws Exception {
        long organizationUserId = jdbc.queryForObject("""
                        SELECT id
                        FROM iam_organization_user
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND organization_user_uid = ?
                        """,
                Long.class,
                ready.tenantId(),
                ready.organizationId(),
                ordinary.organizationUserUid().toString());
        jdbc.update("""
                        INSERT INTO iam_organization_user_capability (
                            tenant_id, organization_id,
                            organization_user_id,
                            capability_code, enabled,
                            granted_at, revoked_at,
                            lock_version, updated_at
                        ) VALUES (
                            ?, ?, ?, 'CLEAN_OPERATION', 1,
                            UTC_TIMESTAMP(3), NULL, 0,
                            UTC_TIMESTAMP(3)
                        )
                        """,
                ready.tenantId(),
                ready.organizationId(),
                organizationUserId);

        MvcResult login = mockMvc.perform(
                        post("/api/v1/miniapp/auth/sessions")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "appId", ordinary.appId(),
                                                "wxLoginCode",
                                                "fake:delivery:" + run
                                                        + ":owner",
                                                "registrationSource",
                                                Map.of(
                                                        "deploymentCode",
                                                        ready.deploymentCode())))))
                .andReturn();
        assertEquals(
                201,
                login.getResponse().getStatus(),
                login.getResponse().getContentAsString());
        JsonNode data = data(login);
        assertEquals(
                "CLEANING",
                data.path("entryMode").asText());
        return new MiniappUser(
                data.path("accessToken").asText(),
                ordinary.organizationUserUid(),
                ordinary.appId());
    }

    private DeliveryEvent trustedCleanComplete(
            ReadyDeployment ready,
            UUID operationUid,
            DeviceCommandSubmission downlink,
            long edgeEventSequence) throws Exception {
        return trustedCleanComplete(
                ready,
                operationUid,
                downlink,
                edgeEventSequence,
                true);
    }

    private DeliveryEvent trustedCleanComplete(
            ReadyDeployment ready,
            UUID operationUid,
            DeviceCommandSubmission downlink,
            long edgeEventSequence,
            boolean stableFinalMeasurement) throws Exception {
        Map<String, Object> frozen = jdbc.queryForMap("""
                SELECT command.command_uid,
                       config.version_no,
                       LOWER(HEX(config.content_sha256))
                           AS content_sha256,
                       LOWER(HEX(config.mcu_payload_sha256))
                           AS mcu_payload_sha256,
                       snapshot.calibration_version,
                       old_bag.bag_uid AS old_bag_uid,
                       new_bag.bag_uid AS new_bag_uid
                FROM rec_clean_operation operation
                JOIN dev_device_command command
                  ON command.clean_operation_id = operation.id
                 AND command.command_type =
                     'START_CLEAN_OPERATION'
                JOIN dev_config_version config
                  ON config.id = operation.device_config_version_id
                JOIN dev_port_config_snapshot snapshot
                  ON snapshot.config_version_id = config.id
                 AND snapshot.port_id = operation.port_id
                LEFT JOIN rec_bag old_bag
                  ON old_bag.id = operation.old_bag_id
                JOIN rec_bag new_bag
                  ON new_bag.id = operation.new_bag_id
                WHERE operation.operation_uid = ?
                """, operationUid.toString());
        assertEquals(
                downlink.commandUid().toString(),
                frozen.get("command_uid").toString());
        long configVersion = ((Number) frozen.get(
                "version_no")).longValue();
        long calibrationVersion = ((Number) frozen.get(
                "calibration_version")).longValue();
        String contentSha256 =
                frozen.get("content_sha256").toString();
        String mcuPayloadSha256 =
                frozen.get("mcu_payload_sha256").toString();
        String oldBagUid = frozen.get("old_bag_uid").toString();
        String newBagUid = frozen.get("new_bag_uid").toString();

        ObjectNode wire = (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet-wire/"
                                        + "clean-complete"
                                        + ".event-wire.json")))
                .path("oneJsonPayload")
                .path("params")
                .path("cleanComplete")
                .path("value")
                .deepCopy();
        String eventUid = UUID.randomUUID().toString();
        String occurredAt = Instant.now()
                .minusSeconds(1)
                .truncatedTo(ChronoUnit.MILLIS)
                .toString();
        wire.put("eventUid", eventUid);
        wire.put("commandUid", downlink.commandUid().toString());
        wire.put("operationUid", operationUid.toString());
        wire.put("deploymentCode", ready.deploymentCode());
        wire.put("edgeEventSequence", edgeEventSequence + 1);
        wire.put("occurredAt", occurredAt);
        wire.put("occurredAtPresent", true);
        ((ObjectNode) wire.path("target"))
                .put("uid", operationUid.toString());
        wire.put("oldBagUidPresent", true);
        wire.put("oldBagUid", oldBagUid);
        wire.put("newBagUid", newBagUid);
        wire.put("removedNetWeightGramsPresent", true);
        wire.put("removedNetWeightGrams", 10_000);
        wire.put(
                "newBaselineWeightGramsPresent",
                stableFinalMeasurement);
        if (stableFinalMeasurement) {
            wire.put("newBaselineWeightGrams", 1_200);
        } else {
            wire.remove("newBaselineWeightGrams");
        }
        ObjectNode wireConfig =
                (ObjectNode) wire.path("frozenConfig");
        wireConfig.put("version", configVersion);
        wireConfig.put("contentSha256", contentSha256);
        wireConfig.put("mcuPayloadSha256", mcuPayloadSha256);
        setStableCleanMeasurement(
                (ObjectNode) wire.path("preUnlockMeasurement"),
                UUID.randomUUID().toString(),
                20_000,
                calibrationVersion,
                31);
        if (stableFinalMeasurement) {
            setStableCleanMeasurement(
                    (ObjectNode) wire.path(
                            "cleanerConfirmedFinalMeasurement"),
                    UUID.randomUUID().toString(),
                    1_200,
                    calibrationVersion,
                    42);
        } else {
            setFailedCleanMeasurement(
                    (ObjectNode) wire.path(
                            "cleanerConfirmedFinalMeasurement"),
                    UUID.randomUUID().toString(),
                    calibrationVersion,
                    42);
        }

        ObjectNode semanticPayload =
                (ObjectNode) objectMapper.readTree(
                                Files.readString(contractPath(
                                        "contracts/examples/onenet/"
                                                + "clean-complete"
                                                + ".event.json")))
                        .path("payload")
                        .deepCopy();
        semanticPayload.put("operationUid", operationUid.toString());
        semanticPayload.put("oldBagUid", oldBagUid);
        semanticPayload.put("newBagUid", newBagUid);
        semanticPayload.put("removedNetWeightGrams", 10_000);
        if (stableFinalMeasurement) {
            semanticPayload.put("newBaselineWeightGrams", 1_200);
        } else {
            semanticPayload.putNull("newBaselineWeightGrams");
        }
        ObjectNode semanticConfig =
                (ObjectNode) semanticPayload.path("frozenConfig");
        semanticConfig.put("version", configVersion);
        semanticConfig.put("contentSha256", contentSha256);
        semanticConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        copyNormalizedMeasurement(
                wire.path("preUnlockMeasurement"),
                (ObjectNode) semanticPayload.path(
                        "preUnlockMeasurement"));
        copyNormalizedMeasurement(
                wire.path("cleanerConfirmedFinalMeasurement"),
                (ObjectNode) semanticPayload.path(
                        "cleanerConfirmedFinalMeasurement"));
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic = objectMapper.convertValue(
                semanticPayload,
                Map.class);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semantic));
        wire.put("payloadSha256", payloadSha256);

        dispatchTrustedWireEvent(
                "cleanComplete",
                wire,
                ready.hardwareSn());
        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "clean-complete-" + eventUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        return new DeliveryEvent(
                eventUid,
                payloadSha256,
                wire);
    }

    private static void setStableCleanMeasurement(
            ObjectNode measurement,
            String measurementUid,
            long weightGrams,
            long calibrationVersion,
            long mcuEventSequence) {
        measurement.put("measurementUid", measurementUid);
        measurement.put("status", 1);
        measurement.put("weightValueAvailable", true);
        measurement.put("reportedWeightGrams", weightGrams);
        measurement.put("reportedWeightGramsPresent", true);
        measurement.put("weightValueKind", 2);
        measurement.put("measurementElapsedMs", 1_200);
        measurement.put("sampleCount", 12);
        measurement.put("calibrationVersion", calibrationVersion);
        measurement.put("sensorHealth", 1);
        measurement.put("faultCodePresent", false);
        measurement.put("mcuBootId", 101);
        measurement.put("mcuEventSequence", mcuEventSequence);
    }

    private static void setFailedCleanMeasurement(
            ObjectNode measurement,
            String measurementUid,
            long calibrationVersion,
            long mcuEventSequence) {
        measurement.put("measurementUid", measurementUid);
        measurement.put("status", 3);
        measurement.put("weightValueAvailable", false);
        measurement.put("reportedWeightGramsPresent", false);
        measurement.remove("reportedWeightGrams");
        measurement.put("weightValueKind", 1);
        measurement.put("measurementElapsedMs", 1_200);
        measurement.put("sampleCount", 0);
        measurement.put("calibrationVersion", calibrationVersion);
        measurement.put("sensorHealth", 2);
        measurement.put("faultCodePresent", true);
        measurement.put("faultCode", 7);
        measurement.put("mcuBootId", 101);
        measurement.put("mcuEventSequence", mcuEventSequence);
    }

    private static void copyNormalizedMeasurement(
            JsonNode wire,
            ObjectNode semantic) {
        semantic.put(
                "measurementUid",
                wire.path("measurementUid").asText());
        boolean weightAvailable = wire.path(
                "weightValueAvailable").asBoolean();
        semantic.put(
                "status",
                switch (wire.path("status").asInt()) {
                    case 1 -> "STABLE";
                    case 3 -> "TIMEOUT";
                    default -> throw new IllegalArgumentException(
                            "unsupported test measurement status");
                });
        semantic.put("weightValueAvailable", weightAvailable);
        if (weightAvailable) {
            semantic.put(
                    "reportedWeightGrams",
                    wire.path("reportedWeightGrams").asLong());
        } else {
            semantic.putNull("reportedWeightGrams");
        }
        semantic.put(
                "weightValueKind",
                wire.path("weightValueKind").asInt() == 2
                        ? "STABLE_WINDOW_MEAN"
                        : "NONE");
        semantic.put(
                "measurementElapsedMs",
                wire.path("measurementElapsedMs").asLong());
        semantic.put(
                "sampleCount",
                wire.path("sampleCount").asInt());
        semantic.put(
                "calibrationVersion",
                wire.path("calibrationVersion").asLong());
        semantic.put(
                "sensorHealth",
                wire.path("sensorHealth").asInt() == 1
                        ? "OK"
                        : "TIMEOUT");
        if (wire.path("faultCodePresent").asBoolean()) {
            semantic.put("faultCode", "WEIGHT_TIMEOUT");
        } else {
            semantic.putNull("faultCode");
        }
        semantic.put("mcuBootId", wire.path("mcuBootId").asLong());
        semantic.put(
                "mcuEventSequence",
                wire.path("mcuEventSequence").asLong());
    }

    private DeliveryEvent trustedDeliveryComplete(
            ReadyDeployment ready,
            UUID sessionUid) throws Exception {
        return trustedDeliveryComplete(ready, sessionUid, 3);
    }

    private DeliveryEvent trustedDeliveryComplete(
            ReadyDeployment ready,
            UUID sessionUid,
            long edgeEventSequence) throws Exception {
        Map<String, Object> frozen = jdbc.queryForMap("""
                SELECT command_row.command_uid,
                       session_row.device_config_version_no,
                       LOWER(HEX(
                           session_row.device_config_content_sha256
                       )) AS content_sha256,
                       LOWER(HEX(
                           session_row.device_config_mcu_payload_sha256
                       )) AS mcu_payload_sha256,
                        CAST(
                            session_row.unit_price_yuan_per_kg
                            * 10000 AS UNSIGNED
                        ) AS unit_price_ten_thousandths,
                        port_config.calibration_version
                 FROM dev_delivery_session session_row
                 JOIN dev_device_command command_row
                   ON command_row.delivery_session_id =
                      session_row.id
                  AND command_row.command_type =
                      'START_DELIVERY_SESSION'
                 JOIN dev_port_config_snapshot port_config
                   ON port_config.id =
                      session_row.port_config_snapshot_id
                 WHERE session_row.session_uid = ?
                 """, sessionUid.toString());
        String commandUid =
                frozen.get("command_uid").toString();
        long configVersion = ((Number) frozen.get(
                "device_config_version_no")).longValue();
        String contentSha256 =
                frozen.get("content_sha256").toString();
        String mcuPayloadSha256 =
                frozen.get("mcu_payload_sha256").toString();
        long unitPrice = ((Number) frozen.get(
                "unit_price_ten_thousandths")).longValue();
        long calibrationVersion = ((Number) frozen.get(
                "calibration_version")).longValue();

        ObjectNode wire = (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet-wire/"
                                        + "delivery-complete"
                                        + ".event-wire.json")))
                .path("oneJsonPayload")
                .path("params")
                .path("deliveryComplete")
                .path("value")
                .deepCopy();
        String eventUid = UUID.randomUUID().toString();
        String occurredAt = Instant.now()
                .minusSeconds(1)
                .truncatedTo(ChronoUnit.MILLIS)
                .toString();
        String beforeMeasurementUid =
                UUID.randomUUID().toString();
        String afterMeasurementUid =
                UUID.randomUUID().toString();
        wire.put("eventUid", eventUid);
        wire.put("edgeEventSequence", edgeEventSequence + 1);
        wire.put("deploymentCode", ready.deploymentCode());
        wire.put("commandUid", commandUid);
        wire.put("sessionUid", sessionUid.toString());
        wire.put("portNo", 2);
        wire.put("unitPriceTenThousandths", unitPrice);
        wire.put("occurredAt", occurredAt);
        ((ObjectNode) wire.path("target"))
                .put("uid", sessionUid.toString());
        ObjectNode wireConfig =
                (ObjectNode) wire.path("frozenConfig");
        wireConfig.put("version", configVersion);
        wireConfig.put("contentSha256", contentSha256);
        wireConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        ((ObjectNode) wire.path("firstPreOpenMeasurement"))
                .put("measurementUid", beforeMeasurementUid)
                .put("calibrationVersion", calibrationVersion);
        ((ObjectNode) wire.path("finalPostCloseMeasurement"))
                .put("measurementUid", afterMeasurementUid)
                .put("calibrationVersion", calibrationVersion);

        ObjectNode semanticPayload =
                (ObjectNode) objectMapper.readTree(
                                Files.readString(contractPath(
                                        "contracts/examples/onenet/"
                                                + "delivery-complete"
                                                + ".event.json")))
                        .path("payload")
                        .deepCopy();
        semanticPayload.put("sessionUid", sessionUid.toString());
        semanticPayload.put("portNo", 2);
        semanticPayload.put(
                "unitPriceTenThousandths", unitPrice);
        ObjectNode semanticConfig =
                (ObjectNode) semanticPayload.path("frozenConfig");
        semanticConfig.put("version", configVersion);
        semanticConfig.put(
                "contentSha256", contentSha256);
        semanticConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        ((ObjectNode) semanticPayload.path(
                "firstPreOpenMeasurement"))
                .put("measurementUid", beforeMeasurementUid)
                .put("calibrationVersion", calibrationVersion);
        ((ObjectNode) semanticPayload.path(
                "finalPostCloseMeasurement"))
                .put("measurementUid", afterMeasurementUid)
                .put("calibrationVersion", calibrationVersion);
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic =
                objectMapper.convertValue(
                        semanticPayload,
                        Map.class);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semantic));
        wire.put("payloadSha256", payloadSha256);

        dispatchTrustedWireEvent(
                "deliveryComplete",
                wire,
                ready.hardwareSn());
        ReliableWorkerBatchResult result =
                inboxWorker.runBatch(
                        "delivery-complete-" + sessionUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        return new DeliveryEvent(
                eventUid,
                payloadSha256,
                wire);
    }

    private FullnessSampleEvent trustedFullnessSampleComplete(
            ReadyDeployment ready,
            String detectionUid,
            String sampleRole,
            boolean full,
            long edgeEventSequence) throws Exception {
        String targetStableKey =
                detectionUid + ":" + sampleRole;
        deferConfirmationTasks(ready.deploymentId());
        assertEquals(1, jdbc.update("""
                        UPDATE ops_reliable_task
                        SET next_run_at = UTC_TIMESTAMP(3),
                            lock_version = lock_version + 1,
                            updated_at = UTC_TIMESTAMP(3)
                        WHERE task_type = 'SAMPLE_FULLNESS'
                          AND target_stable_key = ?
                          AND state = 'PENDING'
                          AND lease_token IS NULL
                        """,
                targetStableKey));

        ReliableWorkerBatchResult submission =
                commandWorker.runBatch(
                        "fullness-" + sampleRole.toLowerCase()
                                + "-" + detectionUid);
        assertEquals(1, submission.claimed());
        assertEquals(1, submission.accepted());
        assertEquals(0, submission.failed());
        DeviceCommandSubmission downlink =
                submissionProbe.lastSubmission();
        assertNotNull(downlink);
        assertEquals("SAMPLE_FULLNESS", downlink.commandType());
        assertEquals(ready.hardwareSn(), downlink.hardwareSn());
        JsonNode commandEnvelope = objectMapper.readTree(
                downlink.semanticEnvelopeJson());
        assertEquals(
                detectionUid,
                commandEnvelope.path("target")
                        .path("uid").asText());
        assertEquals(
                sampleRole,
                commandEnvelope.path("payload")
                        .path("sampleRole").asText());

        Map<String, Object> detection = jdbc.queryForMap("""
                SELECT detection.configured_full_weight_g,
                       detection.baseline_weight_g_snapshot,
                       detection.trigger_type,
                       snapshot.calibration_version
                FROM rec_fullness_detection detection
                JOIN dev_port_config_snapshot snapshot
                  ON snapshot.id =
                     detection.port_config_snapshot_id
                 AND snapshot.tenant_id =
                     detection.tenant_id
                 AND snapshot.organization_id =
                     detection.organization_id
                 AND snapshot.deployment_id =
                     detection.deployment_id
                 AND snapshot.port_id =
                     detection.port_id
                WHERE detection.detection_uid = ?
                """, detectionUid);
        long configuredFullWeight = ((Number) detection.get(
                "configured_full_weight_g")).longValue();
        Number baselineValue = (Number) detection.get(
                "baseline_weight_g_snapshot");
        long baselineWeight = baselineValue == null
                ? 0L : baselineValue.longValue();
        long calibrationVersion = ((Number) detection.get(
                "calibration_version")).longValue();
        String triggerType = detection.get("trigger_type").toString();
        long totalWeight = full
                ? Math.addExact(baselineWeight, configuredFullWeight)
                : Math.addExact(
                        baselineWeight,
                        configuredFullWeight / 2);

        ObjectNode wire = (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet-wire/"
                                        + "fullness-sample-complete"
                                        + ".event-wire.json")))
                .path("oneJsonPayload")
                .path("params")
                .path("fullnessSampleComplete")
                .path("value")
                .deepCopy();
        String eventUid = UUID.randomUUID().toString();
        String measurementUid =
                UUID.randomUUID().toString();
        String occurredAt = Instant.now()
                .minusSeconds(1)
                .truncatedTo(ChronoUnit.MILLIS)
                .toString();
        JsonNode commandPayload =
                commandEnvelope.path("payload");
        JsonNode commandConfig =
                commandPayload.path("config");
        wire.put("eventUid", eventUid);
        wire.put("edgeEventSequence", edgeEventSequence + 1);
        wire.put("deploymentCode", ready.deploymentCode());
        wire.put(
                "commandUid",
                downlink.commandUid().toString());
        wire.put("detectionUid", detectionUid);
        wire.put("occurredAt", occurredAt);
        wire.put("occurredAtPresent", true);
        wire.put("sampleRole",
                "INITIAL".equals(sampleRole) ? 1 : 2);
        wire.put(
                "triggerType",
                "CLEAN_COMPLETE".equals(triggerType) ? 2 : 1);
        wire.put(
                "fullnessMode",
                fullnessModeWireValue(
                        commandPayload.path("fullnessMode")
                                .asText()));
        wire.put("fullnessSensorKind", 2);
        wire.put("fullnessSensorValue", 1);
        wire.put("fullnessSampleBasis", 4);
        wire.put(
                "representativeDistanceMmPresent",
                false);
        wire.remove("representativeDistanceMm");
        wire.put("requestedSampleCount", 1);
        wire.put("validSampleCount", 1);
        ((ObjectNode) wire.path("target"))
                .put("uid", detectionUid);
        ObjectNode wireConfig =
                (ObjectNode) wire.path("frozenConfig");
        wireConfig.put(
                "version",
                commandConfig.path("version").asLong());
        wireConfig.put(
                "contentSha256",
                commandConfig.path("contentSha256").asText());
        wireConfig.put(
                "mcuPayloadSha256",
                commandConfig.path("mcuPayloadSha256")
                        .asText());
        ObjectNode wireMeasurement =
                (ObjectNode) wire.path(
                        "totalWeightMeasurement");
        wireMeasurement.put(
                "measurementUid",
                measurementUid);
        wireMeasurement.put("status", 1);
        wireMeasurement.put(
                "weightValueAvailable",
                true);
        wireMeasurement.put(
                "reportedWeightGrams",
                totalWeight);
        wireMeasurement.put(
                "reportedWeightGramsPresent",
                true);
        wireMeasurement.put("weightValueKind", 2);
        wireMeasurement.put("measurementElapsedMs", 200);
        wireMeasurement.put("sampleCount", 1);
        wireMeasurement.put(
                "calibrationVersion",
                calibrationVersion);
        wireMeasurement.put("sensorHealth", 1);
        wireMeasurement.put("faultCodePresent", false);
        wireMeasurement.put("mcuBootId", 101);
        wireMeasurement.put(
                "mcuEventSequence",
                edgeEventSequence);

        ObjectNode semanticPayload =
                (ObjectNode) objectMapper.readTree(
                                Files.readString(contractPath(
                                        "contracts/examples/onenet/"
                                                + "fullness-sample-complete"
                                                + ".event.json")))
                        .path("payload")
                        .deepCopy();
        semanticPayload.put("detectionUid", detectionUid);
        semanticPayload.put("portNo", 2);
        semanticPayload.put("sampleRole", sampleRole);
        semanticPayload.put(
                "triggerType",
                triggerType);
        semanticPayload.put(
                "fullnessMode",
                commandPayload.path("fullnessMode").asText());
        semanticPayload.put(
                "fullnessSensorKind",
                "DIGITAL_INFRARED");
        semanticPayload.put(
                "fullnessSensorValue",
                "CLEAR");
        semanticPayload.put(
                "fullnessSampleBasis",
                "NOT_SAMPLED");
        semanticPayload.putNull(
                "representativeDistanceMm");
        semanticPayload.put("requestedSampleCount", 1);
        semanticPayload.put("validSampleCount", 1);
        ObjectNode semanticConfig =
                (ObjectNode) semanticPayload.path(
                        "frozenConfig");
        semanticConfig.put(
                "version",
                commandConfig.path("version").asLong());
        semanticConfig.put(
                "contentSha256",
                commandConfig.path("contentSha256").asText());
        semanticConfig.put(
                "mcuPayloadSha256",
                commandConfig.path("mcuPayloadSha256")
                        .asText());
        ObjectNode semanticMeasurement =
                (ObjectNode) semanticPayload.path(
                        "totalWeightMeasurement");
        semanticMeasurement.put(
                "measurementUid",
                measurementUid);
        semanticMeasurement.put("status", "STABLE");
        semanticMeasurement.put(
                "weightValueAvailable",
                true);
        semanticMeasurement.put(
                "reportedWeightGrams",
                totalWeight);
        semanticMeasurement.put(
                "weightValueKind",
                "STABLE_WINDOW_MEAN");
        semanticMeasurement.put(
                "measurementElapsedMs",
                200);
        semanticMeasurement.put("sampleCount", 1);
        semanticMeasurement.put(
                "calibrationVersion",
                calibrationVersion);
        semanticMeasurement.put("sensorHealth", "OK");
        semanticMeasurement.putNull("faultCode");
        semanticMeasurement.put("mcuBootId", 101);
        semanticMeasurement.put(
                "mcuEventSequence",
                edgeEventSequence);
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic =
                objectMapper.convertValue(
                        semanticPayload,
                        Map.class);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semantic));
        wire.put("payloadSha256", payloadSha256);

        dispatchTrustedWireEvent(
                "fullnessSampleComplete",
                wire,
                ready.hardwareSn());
        ReliableWorkerBatchResult result =
                inboxWorker.runBatch(
                        "fullness-complete-" + eventUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        return new FullnessSampleEvent(
                eventUid,
                downlink.commandUid().toString(),
                totalWeight);
    }

    private static int fullnessModeWireValue(String value) {
        return switch (value) {
            case "SENSOR_ONLY" -> 1;
            case "WEIGHT_ONLY" -> 2;
            case "SENSOR_OR_WEIGHT" -> 3;
            default -> throw new IllegalArgumentException(
                    "unsupported fullness mode " + value);
        };
    }

    private PhotoStatusEvent trustedPhotoStatus(
            ReadyDeployment ready,
            UUID sessionUid) throws Exception {
        ObjectNode wire = (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet-wire/"
                                        + "photo-status-reported"
                                        + ".event-wire.json")))
                .path("oneJsonPayload")
                .path("params")
                .path("photoStatusReported")
                .path("value")
                .deepCopy();
        String eventUid = UUID.randomUUID().toString();
        String photoUid = UUID.randomUUID().toString();
        String occurredAt = Instant.now()
                .minusSeconds(1)
                .truncatedTo(ChronoUnit.MILLIS)
                .toString();
        String capturedAt = Instant.now()
                .minusSeconds(2)
                .truncatedTo(ChronoUnit.MILLIS)
                .toString();
        String objectUrl = COS_BASE_URL
                + "/ecobin/"
                + ready.deploymentCode()
                + "/delivery-session/"
                + sessionUid
                + "/AFTER_INNER/"
                + photoUid
                + ".jpg";
        wire.put("eventUid", eventUid);
        wire.put("edgeEventSequence", 3);
        wire.put("deploymentCode", ready.deploymentCode());
        wire.put("occurredAt", occurredAt);
        wire.put("workUid", sessionUid.toString());
        ((ObjectNode) wire.path("target"))
                .put("uid", sessionUid.toString());
        ObjectNode wirePhoto = (ObjectNode) wire.path("photo");
        wirePhoto.put("photoUid", photoUid);
        wirePhoto.put("url", objectUrl);
        wirePhoto.put("capturedAt", capturedAt);

        ObjectNode semanticPayload =
                (ObjectNode) objectMapper.readTree(
                                Files.readString(contractPath(
                                        "contracts/examples/onenet/"
                                                + "photo-status-reported"
                                                + ".event.json")))
                        .path("payload")
                        .deepCopy();
        semanticPayload.put("workUid", sessionUid.toString());
        ObjectNode semanticPhoto =
                (ObjectNode) semanticPayload.path("photo");
        semanticPhoto.put("photoUid", photoUid);
        semanticPhoto.put("url", objectUrl);
        semanticPhoto.put("capturedAt", capturedAt);
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic =
                objectMapper.convertValue(
                        semanticPayload,
                        Map.class);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semantic));
        wire.put("payloadSha256", payloadSha256);

        dispatchTrustedWireEvent(
                "photoStatusReported",
                wire,
                ready.hardwareSn());
        ReliableWorkerBatchResult result =
                inboxWorker.runBatch(
                        "photo-status-" + sessionUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        return new PhotoStatusEvent(
                eventUid,
                photoUid,
                objectUrl);
    }

    private void applyTrustedConfigurationProgress(
            String hardwareSn,
            String deploymentCode,
            String applicationUid) {
        Map<String, Object> target = jdbc.queryForMap("""
                SELECT command_row.command_uid,
                       version.version_no,
                       LOWER(HEX(version.content_sha256))
                           AS content_sha256,
                       LOWER(HEX(version.mcu_payload_sha256))
                           AS mcu_payload_sha256
                FROM dev_config_application application
                JOIN dev_config_version version
                  ON version.id = application.config_version_id
                JOIN dev_device_command command_row
                  ON command_row.config_application_id = application.id
                 AND command_row.command_type = 'APPLY_CONFIGURATION'
                WHERE application.application_uid = ?
                """, applicationUid);
        String commandUid = target.get("command_uid").toString();
        long version = ((Number) target.get("version_no")).longValue();
        String contentSha256 = target.get("content_sha256").toString();
        String mcuPayloadSha256 =
                target.get("mcu_payload_sha256").toString();
        String mcuCommandUid = UUID.randomUUID().toString();

        Map<String, Object> semanticPayload = new LinkedHashMap<>();
        semanticPayload.put("applicationUid", applicationUid);
        semanticPayload.put("contentSha256", contentSha256);
        semanticPayload.put("errorCode", null);
        semanticPayload.put("mcuCommandUid", mcuCommandUid);
        semanticPayload.put("mcuPayloadSha256", mcuPayloadSha256);
        semanticPayload.put("stage", "APPLIED");
        semanticPayload.put("version", version);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));

        Map<String, Object> eventTarget = new LinkedHashMap<>();
        eventTarget.put("type", 1);
        eventTarget.put("uid", applicationUid);
        String eventUid = UUID.randomUUID().toString();
        Map<String, Object> wire = new LinkedHashMap<>();
        wire.put("applicationUid", applicationUid);
        wire.put("clockQuality", 1);
        wire.put("commandUid", commandUid);
        wire.put("contentSha256", contentSha256);
        wire.put("deliveryClass", 1);
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", 1);
        wire.put("errorCode", "");
        wire.put("errorCodePresent", false);
        wire.put("eventType", 1);
        wire.put("eventUid", eventUid);
        wire.put("mcuCommandUid", mcuCommandUid);
        wire.put("mcuCommandUidPresent", true);
        wire.put("mcuPayloadSha256", mcuPayloadSha256);
        wire.put(
                "occurredAt",
                Instant.now().truncatedTo(ChronoUnit.MILLIS).toString());
        wire.put("occurredAtPresent", true);
        wire.put("payloadSha256", payloadSha256);
        wire.put("schemaVersion", 1);
        wire.put("stage", 2);
        wire.put("target", eventTarget);
        wire.put("version", version);

        dispatchTrustedWireEvent(
                "configurationProgress",
                wire,
                hardwareSn);
        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "configuration-progress-" + eventUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
        assertEquals("APPLIED", jdbc.queryForObject("""
                        SELECT status
                        FROM dev_config_application
                        WHERE application_uid = ?
                        """, String.class, applicationUid));
    }

    private void acceptTransportLifecycle(
            String hardwareSn,
            String status,
            long observedAtEpochMillis,
            String externalMessageId) {
        Map<String, Object> subData = new LinkedHashMap<>();
        subData.put("productId", "delivery-integration-product");
        subData.put("deviceName", hardwareSn);
        subData.put("time", observedAtEpochMillis);
        Map<String, Object> decrypted = new LinkedHashMap<>();
        decrypted.put(
                "msgType",
                "ONLINE".equals(status)
                        ? "deviceOnline"
                        : "deviceOffline");
        decrypted.put("subData", subData);

        OneNetProperties properties = new OneNetProperties();
        properties.setProductId("delivery-integration-product");
        CosProperties cosProperties = new CosProperties();
        cosProperties.setBaseUrl(COS_BASE_URL);
        OneNetEventDispatcher dispatcher = new OneNetEventDispatcher(
                trustedInboxPort,
                sourceScopePort,
                properties,
                cosProperties,
                objectMapper);
        dispatcher.handle(
                objectMapper.writeValueAsString(decrypted),
                externalMessageId,
                "encrypted-delivery-lifecycle-envelope"
                        .getBytes(StandardCharsets.UTF_8));
        ReliableWorkerBatchResult result = inboxWorker.runBatch(
                "delivery-lifecycle-" + externalMessageId);
        assertEquals(1, result.claimed());
        assertEquals(1, result.accepted());
        assertEquals(0, result.failed());
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                        """, String.class, externalMessageId));
    }

    private void applyTrustedRuntimeSnapshot(
            String hardwareSn,
            String deploymentCode,
            String applicationUid,
            long edgeEventSequence) throws Exception {
        Map<String, Object> configuration = jdbc.queryForMap("""
                SELECT version.version_no,
                       LOWER(HEX(version.content_sha256))
                           AS content_sha256,
                       LOWER(HEX(version.mcu_payload_sha256))
                           AS mcu_payload_sha256
                FROM dev_config_application application
                JOIN dev_config_version version
                  ON version.id =
                     application.config_version_id
                WHERE application.application_uid = ?
                """, applicationUid);
        long version = ((Number) configuration.get(
                "version_no")).longValue();
        String contentSha256 =
                configuration.get("content_sha256").toString();
        String mcuPayloadSha256 =
                configuration.get("mcu_payload_sha256").toString();

        @SuppressWarnings("unchecked")
        Map<String, Object> semanticEvent =
                objectMapper.readValue(
                        Files.readString(contractPath(
                                "contracts/examples/onenet/"
                                        + "device-runtime-snapshot"
                                        + ".event.json")),
                        Map.class);
        Map<String, Object> semanticPayload =
                mutableMap(semanticEvent.get("payload"));
        Map<String, Object> semanticConfig =
                mutableMap(semanticPayload.get("appliedConfig"));
        semanticConfig.put("version", version);
        semanticConfig.put("contentSha256", contentSha256);
        semanticConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        semanticPayload.put("mcuBootId", null);
        semanticPayload.put("mcuFirmwareVersion", null);
        semanticPayload.put("uartProtocolMajor", null);
        semanticPayload.put("uartProtocolMinor", null);
        semanticPayload.put("uartState", "READY");
        semanticPayload.put("pendingReliableEventCount", 0);
        useFixedFrameRuntimePortFacts(semanticPayload, false);
        String payloadSha256 = canonicalizer.hex(
                canonicalizer.payloadSha256(semanticPayload));

        @SuppressWarnings("unchecked")
        Map<String, Object> fixture =
                objectMapper.readValue(
                        Files.readString(contractPath(
                                "contracts/examples/onenet-wire/"
                                        + "device-runtime-snapshot"
                                        + ".event-wire.json")),
                        Map.class);
        Map<String, Object> oneJson =
                mutableMap(fixture.get("oneJsonPayload"));
        Map<String, Object> params =
                mutableMap(oneJson.get("params"));
        Map<String, Object> eventWrapper =
                mutableMap(params.get("deviceRuntimeSnapshot"));
        Map<String, Object> wire =
                mutableMap(eventWrapper.get("value"));
        Map<String, Object> wireConfig =
                mutableMap(wire.get("appliedConfig"));
        wireConfig.put("version", version);
        wireConfig.put("contentSha256", contentSha256);
        wireConfig.put(
                "mcuPayloadSha256", mcuPayloadSha256);
        wire.put("deploymentCode", deploymentCode);
        wire.put("edgeEventSequence", edgeEventSequence);
        String eventUid = UUID.randomUUID().toString();
        wire.put("eventUid", eventUid);
        wire.put(
                "occurredAt",
                Instant.now().minusSeconds(1)
                        .truncatedTo(ChronoUnit.MILLIS)
                        .toString());
        wire.put("mcuBootIdPresent", false);
        wire.put("mcuFirmwareVersionPresent", false);
        wire.put("uartProtocolMajorPresent", false);
        wire.put("uartProtocolMinorPresent", false);
        wire.remove("mcuBootId");
        wire.remove("mcuFirmwareVersion");
        wire.remove("uartProtocolMajor");
        wire.remove("uartProtocolMinor");
        wire.put("uartState", 3);
        wire.put("pendingReliableEventCount", 0);
        useFixedFrameRuntimePortFacts(wire, true);
        wire.put("payloadSha256", payloadSha256);
        mutableMap(wire.get("target"))
                .put("uid", deploymentCode);

        dispatchTrustedWireEvent(
                "deviceRuntimeSnapshot",
                wire,
                hardwareSn);
        assertEquals("PROCESSED", jdbc.queryForObject("""
                        SELECT processing_state
                        FROM ops_inbox_message
                        WHERE external_message_id = ?
                          AND message_kind =
                              'DEVICE_RUNTIME_SNAPSHOT'
                        """, String.class, eventUid));
        Map<String, Object> appliedRuntime = jdbc.queryForMap("""
                SELECT edge_event.event_uid
                           AS trusted_runtime_edge_event_uid,
                       runtime.trusted_runtime_sequence,
                       runtime.uart_state,
                       runtime.applied_config_version_no
                FROM dev_deployment_runtime_state runtime
                JOIN dev_device_deployment deployment
                  ON deployment.id = runtime.deployment_id
                JOIN dev_edge_event edge_event
                  ON edge_event.id =
                     runtime.trusted_runtime_edge_event_id
                WHERE deployment.public_code = ?
                """, deploymentCode);
        assertEquals(
                eventUid,
                appliedRuntime.get(
                        "trusted_runtime_edge_event_uid").toString());
        assertEquals(
                edgeEventSequence,
                ((Number) appliedRuntime.get(
                        "trusted_runtime_sequence")).longValue());
        assertEquals("READY", appliedRuntime.get("uart_state"));
        assertEquals(
                version,
                ((Number) appliedRuntime.get(
                        "applied_config_version_no")).longValue());
    }

    private void dispatchTrustedWireEvent(
            String identifier,
            Object wire,
            String hardwareSn) {
        Map<String, Object> params =
                Map.of(identifier, Map.of("value", wire));
        Map<String, Object> subData = new LinkedHashMap<>();
        subData.put(
                "productId",
                "delivery-integration-product");
        subData.put("deviceName", hardwareSn);
        subData.put("params", params);
        Map<String, Object> decrypted = new LinkedHashMap<>();
        decrypted.put("msgType", "thingEvent");
        decrypted.put("subData", subData);

        OneNetProperties properties = new OneNetProperties();
        properties.setProductId(
                "delivery-integration-product");
        CosProperties cosProperties = new CosProperties();
        cosProperties.setBaseUrl(COS_BASE_URL);
        OneNetEventDispatcher dispatcher =
                new OneNetEventDispatcher(
                        trustedInboxPort,
                        sourceScopePort,
                        properties,
                        cosProperties,
                        objectMapper);
        String eventUid = objectMapper.valueToTree(wire)
                .path("eventUid").asText();
        dispatcher.handle(
                objectMapper.writeValueAsString(decrypted),
                "delivery-integration-message-" + eventUid,
                ("encrypted-delivery-envelope-" + eventUid)
                        .getBytes(StandardCharsets.UTF_8));
    }

    private String inboxFailureDiagnostic(String eventUid) {
        return jdbc.queryForObject("""
                        SELECT attempt.redacted_diagnostic
                        FROM ops_task_attempt attempt
                        JOIN ops_reliable_task task
                          ON task.id = attempt.task_id
                        JOIN ops_inbox_message inbox
                          ON inbox.id = task.source_inbox_id
                        WHERE inbox.external_message_id = ?
                        ORDER BY attempt.id DESC
                        LIMIT 1
                        """,
                String.class,
                eventUid);
    }

    private void deferConfirmationTasks(long deploymentId) {
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at =
                        DATE_ADD(
                            UTC_TIMESTAMP(3),
                            INTERVAL 1 DAY
                        ),
                    updated_at = UTC_TIMESTAMP(3),
                    lock_version = lock_version + 1
                WHERE task_type = 'CONFIRM_EDGE_EVENT'
                  AND source_device_deployment_id = ?
                  AND state = 'PENDING'
                  AND lease_token IS NULL
                """,
                deploymentId);
    }

    private void deferPriorDeliveryFixtures() {
        jdbc.update("""
                UPDATE ops_reliable_task task
                LEFT JOIN dev_device_deployment deployment
                  ON deployment.tenant_id = task.tenant_id
                 AND deployment.organization_id =
                     task.organization_id
                 AND deployment.id =
                     task.source_device_deployment_id
                LEFT JOIN dev_device_asset asset
                  ON asset.id = deployment.asset_id
                LEFT JOIN ops_inbox_message inbox
                  ON inbox.id = task.source_inbox_id
                SET task.next_run_at =
                        DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 DAY),
                    task.updated_at = UTC_TIMESTAMP(3),
                    task.lock_version = task.lock_version + 1
                WHERE task.state = 'PENDING'
                  AND task.lease_token IS NULL
                  AND (
                      asset.hardware_sn LIKE 'HW-DELIVERY-%'
                      OR asset.hardware_sn LIKE 'HW-DEVICE-%'
                      OR inbox.source_principal_key LIKE
                          '%HW-DELIVERY-%'
                      OR inbox.source_principal_key LIKE
                          '%HW-DEVICE-%'
                  )
                """);
    }

    private void deferCurrentDeliveryFixture() {
        if (run == null) {
            return;
        }
        String hardwareSn = "HW-DELIVERY-" + run;
        jdbc.update("""
                UPDATE ops_reliable_task task
                LEFT JOIN dev_device_deployment deployment
                  ON deployment.tenant_id = task.tenant_id
                 AND deployment.organization_id =
                     task.organization_id
                 AND deployment.id =
                     task.source_device_deployment_id
                LEFT JOIN dev_device_asset asset
                  ON asset.id = deployment.asset_id
                LEFT JOIN ops_inbox_message inbox
                  ON inbox.id = task.source_inbox_id
                SET task.next_run_at =
                        DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 DAY),
                    task.updated_at = UTC_TIMESTAMP(3),
                    task.lock_version = task.lock_version + 1
                WHERE task.state = 'PENDING'
                  AND task.lease_token IS NULL
                  AND (
                      asset.hardware_sn = ?
                      OR inbox.source_principal_key = ?
                  )
                """, hardwareSn, hardwareSn);
    }

    private void createEnabledScope(
            BrowserClient platform,
            String tenantCode,
            String organizationCode,
            String principalLogin) throws Exception {
        data(write(
                platform,
                post("/api/v1/web/platform/tenants"),
                UUID.randomUUID(),
                Map.of(
                        "tenantCode", tenantCode,
                        "enterpriseName",
                        "Delivery test tenant"),
                201));
        write(
                platform,
                post("/api/v1/web/platform/tenants/"
                        + tenantCode + "/principal-account"),
                UUID.randomUUID(),
                Map.of(
                        "loginName", principalLogin,
                        "initialPassword", PRINCIPAL_PASSWORD,
                        "displayName", "Delivery principal",
                        "expectedVersion", 0),
                201);
        long tenantVersion = data(read(
                platform,
                "/api/v1/web/platform/tenants/"
                        + tenantCode,
                200)).path("version").asLong();
        write(
                platform,
                post("/api/v1/web/platform/tenants/"
                        + tenantCode + "/activations"),
                UUID.randomUUID(),
                Map.of("expectedVersion", tenantVersion),
                200);
        JsonNode organization = data(write(
                platform,
                post("/api/v1/web/platform/tenants/"
                        + tenantCode + "/organizations"),
                UUID.randomUUID(),
                Map.of(
                        "organizationCode", organizationCode,
                        "organizationName",
                        "Delivery test organization"),
                201));
        write(
                platform,
                post("/api/v1/web/platform/tenants/"
                        + tenantCode + "/organizations/"
                        + organizationCode + "/activations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion",
                        organization.path("version").asLong()),
                200);
    }

    private MvcResult login(
            BrowserClient client,
            String path,
            String loginName,
            String password,
            int expectedStatus) throws Exception {
        return write(
                client,
                post(path),
                null,
                Map.of(
                        "loginName", loginName,
                        "password", password),
                expectedStatus);
    }

    private MvcResult read(
            BrowserClient client,
            String path,
            int expectedStatus) throws Exception {
        MvcResult result = mockMvc.perform(
                        withCookies(
                                client,
                                get(path)))
                .andReturn();
        client.accept(result);
        assertEquals(
                expectedStatus,
                result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private JsonNode miniappRead(
            MiniappUser user,
            String path,
            int expectedStatus) throws Exception {
        return data(miniappReadResult(user, path, expectedStatus));
    }

    private MvcResult miniappReadResult(
            MiniappUser user,
            String path,
            int expectedStatus) throws Exception {
        MvcResult result = mockMvc.perform(
                        get(path).header(
                                "Authorization",
                                "Bearer " + user.accessToken()))
                .andReturn();
        assertEquals(
                expectedStatus,
                result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private List<MvcResult> concurrentWrites(
            BrowserClient client,
            String path,
            UUID firstOperationUid,
            UUID secondOperationUid,
            Object body) throws Exception {
        String csrfToken = csrf(client);
        Cookie[] requestCookies = client.cookies.values()
                .toArray(Cookie[]::new);
        byte[] requestBody =
                objectMapper.writeValueAsBytes(body);
        CountDownLatch readyGate = new CountDownLatch(2);
        CountDownLatch startGate = new CountDownLatch(1);
        var executor = Executors.newFixedThreadPool(2);
        var first = executor.submit(
                () -> concurrentWrite(
                        path,
                        firstOperationUid,
                        csrfToken,
                        requestCookies,
                        requestBody,
                        readyGate,
                        startGate));
        var second = executor.submit(
                () -> concurrentWrite(
                        path,
                        secondOperationUid,
                        csrfToken,
                        requestCookies,
                        requestBody,
                        readyGate,
                        startGate));
        try {
            assertTrue(
                    readyGate.await(10, TimeUnit.SECONDS),
                    "concurrent review requests did not become ready");
            startGate.countDown();
            return List.of(
                    first.get(30, TimeUnit.SECONDS),
                    second.get(30, TimeUnit.SECONDS));
        } finally {
            startGate.countDown();
            executor.shutdownNow();
            assertTrue(
                    executor.awaitTermination(
                            10,
                            TimeUnit.SECONDS),
                    "concurrent review executor did not terminate");
        }
    }

    private MvcResult concurrentWrite(
            String path,
            UUID operationUid,
            String csrfToken,
            Cookie[] requestCookies,
            byte[] requestBody,
            CountDownLatch readyGate,
            CountDownLatch startGate) throws Exception {
        readyGate.countDown();
        if (!startGate.await(10, TimeUnit.SECONDS)) {
            throw new IllegalStateException(
                    "concurrent review start gate timed out");
        }
        MockHttpServletRequestBuilder builder = post(path)
                .header("X-CSRF-TOKEN", csrfToken)
                .header(
                        "Idempotency-Key",
                        operationUid.toString())
                .contentType(MediaType.APPLICATION_JSON)
                .content(requestBody);
        if (requestCookies.length > 0) {
            builder.cookie(requestCookies);
        }
        return mockMvc.perform(builder).andReturn();
    }

    private MvcResult write(
            BrowserClient client,
            MockHttpServletRequestBuilder builder,
            UUID operationUid,
            Object body,
            int expectedStatus) throws Exception {
        builder.header("X-CSRF-TOKEN", csrf(client));
        if (operationUid != null) {
            builder.header(
                    "Idempotency-Key",
                    operationUid.toString());
        }
        MvcResult result = mockMvc.perform(
                        withCookies(client, builder)
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(
                                        objectMapper.writeValueAsBytes(
                                                body)))
                .andReturn();
        client.accept(result);
        assertEquals(
                expectedStatus,
                result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return result;
    }

    private String csrf(BrowserClient client)
            throws Exception {
        MvcResult result = mockMvc.perform(
                        withCookies(
                                client,
                                get("/api/v1/web/auth/csrf-token")))
                .andReturn();
        client.accept(result);
        assertEquals(
                200,
                result.getResponse().getStatus(),
                result.getResponse().getContentAsString());
        return data(result).path("token").asText();
    }

    private MockHttpServletRequestBuilder withCookies(
            BrowserClient client,
            MockHttpServletRequestBuilder builder) {
        if (!client.cookies.isEmpty()) {
            builder.cookie(
                    client.cookies.values()
                            .toArray(Cookie[]::new));
        }
        return builder;
    }

    private JsonNode data(MvcResult result)
            throws Exception {
        return objectMapper.readTree(
                        result.getResponse().getContentAsByteArray())
                .path("data");
    }

    private String code(String prefix) {
        return prefix + "-" + run;
    }

    private static String digits(
            String seed,
            int length) {
        StringBuilder digits = new StringBuilder();
        for (char character : seed.toCharArray()) {
            digits.append(
                    Math.floorMod(character, 10));
        }
        while (digits.length() < length) {
            digits.append('7');
        }
        return digits.substring(0, length);
    }

    private static boolean hasResultReference(
            JsonNode envelope,
            String type,
            String key) {
        for (JsonNode reference : envelope.path("payload")
                .path("resultReferences")) {
            if (type.equals(reference.path("type").asText())
                    && key.equals(
                            reference.path("key").asText())) {
                return true;
            }
        }
        return false;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> mutableMap(
            Object value) {
        return (Map<String, Object>) value;
    }

    private static void useFixedFrameRuntimePortFacts(
            Map<String, Object> payload,
            boolean wireShape) {
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> ports =
                (List<Map<String, Object>>) payload.get("ports");
        for (Map<String, Object> port : ports) {
            port.put("calibrationVersion", 0);
            port.put(
                    "fullnessSensorKind",
                    wireShape ? 2 : "DIGITAL_INFRARED");
            port.put(
                    "fullnessSampleBasis",
                    wireShape ? 4 : "NOT_SAMPLED");
            port.put("fullnessValidSampleCount", 1);
            if (wireShape) {
                port.remove("representativeDistanceMm");
                port.put(
                        "representativeDistanceMmPresent",
                        false);
            } else {
                port.put("representativeDistanceMm", null);
            }
        }
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("")
                .toAbsolutePath()
                .normalize();
        Path repository = Files.isDirectory(
                workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256")
                    .digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private record ReadyDeployment(
            String tenantCode,
            String organizationCode,
            String hardwareSn,
            String deploymentCode,
            long tenantId,
            long organizationId,
            long deploymentId,
            long portId) {
    }

    private record MiniappUser(
            String accessToken,
            UUID organizationUserUid,
            String appId) {
    }

    private record WithdrawalSupport(
            long tenantId,
            long organizationId,
            long organizationUserId,
            long walletId,
            long payoutAccountId,
            long withdrawConfigId,
            long bindingId,
            long organizationMiniappId,
            long merchantId,
            String mchid,
            String appid,
            String openid) {
    }

    private record DeliveryEvent(
            String eventUid,
            String payloadSha256,
            ObjectNode wire) {
    }

    private record FullnessSampleEvent(
            String eventUid,
            String commandUid,
            long totalWeightGrams) {
    }

    private record CleanOperationStarted(
            UUID operationUid,
            DeviceCommandSubmission downlink) {
    }

    private record PhotoStatusEvent(
            String eventUid,
            String photoUid,
            String objectUrl) {
    }

    private static final class BrowserClient {

        private final Map<String, Cookie> cookies =
                new LinkedHashMap<>();

        private void accept(MvcResult result) {
            for (Cookie cookie :
                    result.getResponse().getCookies()) {
                if (cookie.getMaxAge() == 0) {
                    cookies.remove(cookie.getName());
                } else {
                    cookies.put(cookie.getName(), cookie);
                }
            }
        }
    }
}
