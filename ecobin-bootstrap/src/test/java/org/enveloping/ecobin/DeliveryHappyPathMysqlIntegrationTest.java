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
    void normalDeliveryCreatesExactlyOnePendingOrderAndNextDeliveryGate()
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

        Map<String, Object> detection = jdbc.queryForMap("""
                SELECT detection.id,
                       detection.detection_uid,
                       detection.status,
                       detection.disposition,
                       detection.active_port_id
                FROM rec_fullness_detection detection
                WHERE detection.delivery_order_id = ?
                """, order.get("id"));
        assertEquals(
                "PENDING_INITIAL_SAMPLE",
                detection.get("status").toString());
        assertEquals(
                "PENDING",
                detection.get("disposition").toString());
        assertNotNull(detection.get("active_port_id"));
        assertEquals("PENDING|" + detection.get("id"),
                jdbc.queryForObject("""
                        SELECT CONCAT(
                            detection_gate, '|',
                            current_detection_id
                        )
                        FROM rec_port_capacity_state
                        WHERE port_id = ?
                        """, String.class, ready.portId()));

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
        assertTrue(
                hasResultReference(
                        confirmationEnvelope,
                        "FULLNESS_DETECTION",
                        detection.get("detection_uid").toString()));

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
        assertEquals(1, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_fullness_detection
                        WHERE delivery_order_id = ?
                        """, Integer.class, order.get("id")));

        completeAndAssertNotFullDetection(
                ready,
                detection.get("detection_uid").toString(),
                4);

        assertDeliveryOrderQueryAndReviewFlow(
                ready,
                miniappUser,
                sessionUid,
                deliveryEvent,
                ((Number) order.get("id")).longValue(),
                order.get("delivery_order_no").toString());

        assertFullDetectionRequiresConfirmation(
                ready,
                miniappUser,
                5);
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

        Map<String, Object> initialReview = Map.of(
                "expectedRevisionNo", 0,
                "decision", "ORIGINAL_APPROVED",
                "reason", "delivery integration initial review");
        UUID reviewOperationUid = UUID.randomUUID();
        List<MvcResult> concurrentReviewResults =
                concurrentWrites(
                        platform,
                        orderBase + "/" + deliveryOrderNo + "/reviews",
                        reviewOperationUid,
                        initialReview);
        for (MvcResult result : concurrentReviewResults) {
            assertEquals(
                    201,
                    result.getResponse().getStatus(),
                    result.getResponse().getContentAsString());
        }
        JsonNode reviewed = data(concurrentReviewResults.get(0));
        JsonNode replayed = data(concurrentReviewResults.get(1));
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

        JsonNode approvedDetail = data(read(
                platform,
                orderBase + "/" + deliveryOrderNo,
                200));
        assertEquals(
                "APPROVED",
                approvedDetail.path("review").path("status").asText());
        assertEquals(
                2,
                approvedDetail.path("review")
                        .path("currentRevisionNo").asLong());
        assertEquals(
                "2.00",
                approvedDetail.path("review")
                        .path("finalWeightKg").asText());
        assertEquals(2, approvedDetail.path("revisions").size());
        assertEquals(
                "INITIAL_REVIEW",
                approvedDetail.path("revisions").get(0)
                        .path("revisionType").asText());
        assertEquals(
                "CORRECTION",
                approvedDetail.path("revisions").get(1)
                        .path("revisionType").asText());
        assertEquals(
                "0.34",
                approvedDetail.path("revisions").get(1)
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
                "2.00",
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
                "2.00",
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
        createEnabledScope(
                platform,
                tenantCode,
                organizationCode,
                "delivery-principal-" + run);

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
        String deploymentBase = "/api/v1/web/platform/tenants/"
                + tenantCode + "/organizations/" + organizationCode
                + "/device-deployments";
        JsonNode deployment = data(write(
                platform,
                post(deploymentBase),
                UUID.randomUUID(),
                Map.of(
                        "hardwareSn", hardwareSn,
                        "expectedAssetVersion",
                        asset.path("version").asLong()),
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

        jdbc.update("""
                UPDATE dev_config_application application
                JOIN dev_config_version version
                  ON version.id =
                     application.config_version_id
                SET application.status = 'APPLIED',
                    application.reported_version_no =
                        version.version_no,
                    application.reported_content_sha256 =
                        version.content_sha256,
                    application.reported_mcu_payload_sha256 =
                        version.mcu_payload_sha256,
                    application.edge_persisted_at =
                        UTC_TIMESTAMP(3),
                    application.mcu_synced_at =
                        UTC_TIMESTAMP(3),
                    application.applied_at =
                        UTC_TIMESTAMP(3),
                    application.updated_at =
                        UTC_TIMESTAMP(3),
                    application.lock_version =
                        application.lock_version + 1
                WHERE application.application_uid = ?
                """, applicationUid);
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at =
                        DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 DAY),
                    updated_at = UTC_TIMESTAMP(3),
                    lock_version = lock_version + 1
                WHERE task_type = 'ENSURE_DEVICE_CONFIGURATION'
                  AND target_stable_key = ?
                """, applicationUid);

        applyTrustedRuntimeSnapshot(
                hardwareSn,
                deploymentCode,
                applicationUid,
                1);
        data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/activations"),
                UUID.randomUUID(),
                Map.of(
                        "expectedVersion", 0,
                        "expectedConfigurationVersion", 1,
                        "acceptanceConfirmed", true,
                        "reason",
                        "trusted Orange Pi delivery fixture"),
                200));
        data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
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
                ((Number) ids.get("deployment_id")).longValue(),
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
                            display_name, login_enabled, secret_ref,
                            activated_at, lock_version,
                            configured_at, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 'Delivery miniapp',
                            1, 'fake:credential',
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
        wire.put("edgeEventSequence", edgeEventSequence);
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
        long calibrationVersion = ((Number) detection.get(
                "calibration_version")).longValue();
        long totalWeight = full
                ? configuredFullWeight
                : configuredFullWeight / 2;

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
        wire.put("edgeEventSequence", edgeEventSequence);
        wire.put("deploymentCode", ready.deploymentCode());
        wire.put(
                "commandUid",
                downlink.commandUid().toString());
        wire.put("detectionUid", detectionUid);
        wire.put("occurredAt", occurredAt);
        wire.put("occurredAtPresent", true);
        wire.put("sampleRole",
                "INITIAL".equals(sampleRole) ? 1 : 2);
        wire.put("triggerType", 1);
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
                "DELIVERY_COMPLETE");
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
        wire.put("edgeEventSequence", 2);
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
        semanticPayload.put("uartState", "FAULT");
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
        wire.put("uartState", 5);
        useFixedFrameRuntimePortFacts(wire, true);
        wire.put("payloadSha256", payloadSha256);
        mutableMap(wire.get("target"))
                .put("uid", deploymentCode);

        dispatchTrustedWireEvent(
                "deviceRuntimeSnapshot",
                wire,
                hardwareSn);
        ReliableWorkerBatchResult result =
                inboxWorker.runBatch(
                        "runtime-" + eventUid);
        assertEquals(1, result.claimed());
        assertEquals(
                0,
                result.failed(),
                () -> inboxFailureDiagnostic(eventUid));
        assertEquals(1, result.accepted());
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
            UUID operationUid,
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
                        operationUid,
                        csrfToken,
                        requestCookies,
                        requestBody,
                        readyGate,
                        startGate));
        var second = executor.submit(
                () -> concurrentWrite(
                        path,
                        operationUid,
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
