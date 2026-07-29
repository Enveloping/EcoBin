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
 * reliable inbox worker. Assertions are made against the resulting database
 * facts, not against mocks of the device/recycling services.</p>
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
        assertEquals(4, jdbc.queryForObject("""
                        SELECT COUNT(*)
                        FROM rec_delivery_photo
                        WHERE delivery_order_id = ?
                          AND status = 'UPLOAD_PENDING'
                          AND missing_reason = 'CAMERA_NOT_READY'
                        """, Integer.class, order.get("id")));
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
        JsonNode application = data(write(
                platform,
                post(deploymentBase + "/" + deploymentCode
                        + "/configuration-releases"),
                UUID.randomUUID(),
                configurationBody(),
                202));

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
                """, application.path("applicationUid").asText());
        jdbc.update("""
                UPDATE ops_reliable_task
                SET next_run_at =
                        DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 DAY),
                    updated_at = UTC_TIMESTAMP(3),
                    lock_version = lock_version + 1
                WHERE task_type = 'ENSURE_DEVICE_CONFIGURATION'
                  AND target_stable_key = ?
                """, application.path("applicationUid").asText());

        applyTrustedRuntimeSnapshot(
                hardwareSn,
                deploymentCode,
                application.path("applicationUid").asText(),
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
        byte[] deliveryRule =
                sha256(("delivery-rule-" + run)
                        .getBytes(StandardCharsets.UTF_8));
        jdbc.update("""
                        INSERT INTO rec_organization_delivery_config (
                            tenant_id, organization_id, version_no,
                            content_sha256, review_mode,
                            open_balance_floor_cent,
                            max_review_abs_weight_g,
                            publication_source,
                            published_by_staff_account_id,
                            published_at, created_at
                        ) VALUES (
                            ?, ?, 1, ?, 'ALL_MANUAL',
                            -1000, 100000, 'SYSTEM', NULL,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                ready.tenantId(),
                ready.organizationId(),
                deliveryRule);
        long deliveryConfigId = jdbc.queryForObject("""
                        SELECT id
                        FROM rec_organization_delivery_config
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND version_no = 1
                        """,
                Long.class,
                ready.tenantId(),
                ready.organizationId());
        jdbc.update("""
                        INSERT INTO
                            rec_organization_delivery_config_head (
                                organization_id, tenant_id,
                                current_config_id, current_version_no,
                                lock_version, switched_at, updated_at
                            ) VALUES (
                                ?, ?, ?, 1, 0,
                                UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                            )
                        """,
                ready.organizationId(),
                ready.tenantId(),
                deliveryConfigId);
        jdbc.update("""
                        INSERT INTO rec_organization_order_counter (
                            organization_id, tenant_id,
                            last_visibility_sequence_no,
                            lock_version, updated_at
                        ) VALUES (?, ?, 0, 0, UTC_TIMESTAMP(3))
                        """,
                ready.organizationId(),
                ready.tenantId());

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
        MvcResult login = mockMvc.perform(
                        post("/api/v1/miniapp/auth/sessions")
                                .contentType(MediaType.APPLICATION_JSON)
                                .content(objectMapper.writeValueAsBytes(
                                        Map.of(
                                                "appId", appId,
                                                "wxLoginCode",
                                                "fake:delivery:" + run,
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
                                                "fake-phone:+86139"
                                                        + digits(run, 8)))))
                .andReturn();
        assertEquals(
                201,
                phone.getResponse().getStatus(),
                phone.getResponse().getContentAsString());
        return new MiniappUser(accessToken, userUid);
    }

    private DeliveryEvent trustedDeliveryComplete(
            ReadyDeployment ready,
            UUID sessionUid) throws Exception {
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
                       ) AS unit_price_ten_thousandths
                FROM dev_delivery_session session_row
                JOIN dev_device_command command_row
                  ON command_row.delivery_session_id =
                     session_row.id
                 AND command_row.command_type =
                     'START_DELIVERY_SESSION'
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
        wire.put("edgeEventSequence", 2);
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
                .put("measurementUid", beforeMeasurementUid);
        ((ObjectNode) wire.path("finalPostCloseMeasurement"))
                .put("measurementUid", afterMeasurementUid);

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
                .put("measurementUid", beforeMeasurementUid);
        ((ObjectNode) semanticPayload.path(
                "finalPostCloseMeasurement"))
                .put("measurementUid", afterMeasurementUid);
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

    private Map<String, Object> configurationBody() {
        Map<String, Object> device = new LinkedHashMap<>();
        device.put("displayName", "投递闭环测试设备");
        device.put("address", "集成测试位置");
        device.put("longitude", "113.9345000");
        device.put("latitude", "22.5401000");
        device.put("edgeHeartbeatIntervalMs", 30_000);
        device.put("edgeHeartbeatMissThreshold", 3);
        device.put("mcuHeartbeatIntervalMs", 5_000);
        device.put("mcuHeartbeatMissThreshold", 3);
        device.put("doorCloseRetryLimit", 3);
        device.put("continueDeliveryWaitMs", 30_000);
        device.put("negativeWeightThresholdGram", 500);
        device.put("deliveryAutoCloseMs", 120_000);
        device.put("weightMeasurementTimeoutMs", 6_000);
        device.put("deliveryDoorTravelWaitMs", 30_000);
        device.put("cleanSolenoidPulseMs", 1_000);
        device.put("smokeMonitoringEnabled", true);

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("expectedLatestVersion", 0);
        body.put("reason", "delivery happy-path fixture");
        body.put("locationCorrectionConfirmed", false);
        body.put("device", device);
        body.put(
                "ports",
                List.of(
                        port(1, "0.4501"),
                        port(2, "0.4502")));
        return body;
    }

    private static Map<String, Object> port(
            int portNo,
            String unitPrice) {
        Map<String, Object> port = new LinkedHashMap<>();
        port.put("portNo", portNo);
        port.put("displayName", "投口" + portNo);
        port.put("enabled", true);
        port.put("unitPriceYuanPerKg", unitPrice);
        port.put("fullnessMode", "INFRARED_OR_WEIGHT");
        port.put("fullnessWeightKg", "50.000");
        port.put("deliverySettleDelayMs", 3_000);
        port.put("fullnessInitialDelayMs", 5_000);
        port.put("fullnessRecheckDelayMs", 10_000);
        port.put("doorAutoCloseTimeoutMs", 60_000);
        port.put("fullnessSensorKind", "ULTRASONIC");
        port.put("fullnessDistanceThresholdMm", 600);
        port.put("fullnessSampleCount", 5);
        port.put("fullnessMinimumValidSampleCount", 3);
        port.put("fullnessEchoTimeoutUs", 30_000);
        port.put("weightStableWindowMs", 1_500);
        port.put("weightMaximumFluctuationGram", 20);
        port.put("weightRequiredSampleCount", 10);
        port.put("weightMeasurementTimeoutMs", 6_000);
        port.put("weightMinimumGram", -5_000);
        port.put("weightMaximumGram", 100_000);
        port.put("calibrationVersion", 4);
        port.put("infraredSampleTimeoutMs", 3_000);
        port.put("deliveryDoorOperationTimeoutMs", 60_000);
        return port;
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
            UUID organizationUserUid) {
    }

    private record DeliveryEvent(
            String eventUid,
            String payloadSha256,
            ObjectNode wire) {
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
