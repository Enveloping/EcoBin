package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpEntity;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetClientReliableSubmissionTest {

    private static final UUID TASK_UID =
            UUID.fromString("30000000-0000-4000-8000-000000000001");
    private static final UUID COMMAND_UID =
            UUID.fromString("20000000-0000-4000-8000-000000000001");
    private static final String HARDWARE_SN = "SN-CONTRACT-0001";
    private static final String PRODUCT_ID = "contract-product";

    private final ObjectMapper objectMapper = new ObjectMapper();
    private RestTemplate restTemplate;
    private CosUploadCredentialPort cosUploadCredentialPort;
    private OneNetClient client;

    @BeforeEach
    void setUp() {
        OneNetProperties properties = new OneNetProperties();
        properties.setBaseUrl("https://onenet.invalid");
        properties.setInvokeServicePath("/thingmodel/call-service");
        properties.setProductId(PRODUCT_ID);
        properties.setAccessKey("c2FtcGxlLWtleQ==");
        restTemplate = mock(RestTemplate.class);
        cosUploadCredentialPort =
                mock(CosUploadCredentialPort.class);
        when(cosUploadCredentialPort.issue(
                anyString(),
                eq(2),
                anyString()))
                .thenReturn(new CosUploadCredential(
                        "TMP_SECRET_ID",
                        "TMP_SECRET_KEY",
                        "SESSION_TOKEN",
                        Instant.parse(
                                        "2026-07-24T01:00:00Z")
                                .getEpochSecond(),
                        Instant.parse(
                                        "2026-07-24T01:30:00Z")
                                .getEpochSecond(),
                        "ecobin-contract-1250000000",
                        "ap-guangzhou",
                                "https://ecobin-contract-1250000000"
                                + ".cos.ap-guangzhou.myqcloud.com"));
        when(cosUploadCredentialPort.issue(
                anyString(),
                eq(1),
                anyString()))
                .thenReturn(new CosUploadCredential(
                        "TMP_SECRET_ID",
                        "TMP_SECRET_KEY",
                        "SESSION_TOKEN",
                        Instant.parse(
                                        "2026-07-24T01:00:00Z")
                                .getEpochSecond(),
                        Instant.parse(
                                        "2026-07-24T01:30:00Z")
                                .getEpochSecond(),
                        "ecobin-contract-1250000000",
                        "ap-guangzhou",
                        "https://ecobin-contract-1250000000"
                                + ".cos.ap-guangzhou.myqcloud.com"));
        when(cosUploadCredentialPort.issue(
                anyString(),
                isNull(),
                anyString()))
                .thenReturn(new CosUploadCredential(
                        "TMP_SECRET_ID",
                        "TMP_SECRET_KEY",
                        "SESSION_TOKEN",
                        Instant.parse(
                                        "2026-07-24T01:00:00Z")
                                .getEpochSecond(),
                        Instant.parse(
                                        "2026-07-24T01:30:00Z")
                                .getEpochSecond(),
                        "ecobin-contract-1250000000",
                        "ap-guangzhou",
                        "https://ecobin-contract-1250000000"
                                + ".cos.ap-guangzhou.myqcloud.com"));
        client = new OneNetClient(
                properties,
                restTemplate,
                cosUploadCredentialPort,
                objectMapper,
                new OneNetDiagnosticLogger(
                        new DiagnosticLoggingProperties(),
                        new DiagnosticPayloadSanitizer(objectMapper)));
    }

    @Test
    void projectsFrozenConfigurationEnvelopeToGeneratedWireContract()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "apply-configuration.command.json"));
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(envelope, COMMAND_UID));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        assertEquals(200, result.httpStatus());
        assertNotNull(result.requestSha256());
        assertNotNull(result.responseSha256());

        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                eq("https://onenet.invalid/thingmodel/call-service"),
                request.capture(),
                eq(String.class));
        assertNotNull(
                request.getValue().getHeaders().getFirst("Authorization"));

        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "apply-configuration.service-wire.json")));
        ObjectNode expected = (ObjectNode) wireExample
                .path("callServiceApiBodyTemplate")
                .deepCopy();
        expected.put("product_id", PRODUCT_ID);
        expected.put("device_name", HARDWARE_SN);
        assertEquals(expected, actual);
        assertArrayEquals(
                MessageDigest.getInstance("SHA-256")
                        .digest(objectMapper.writeValueAsBytes(
                                request.getValue().getBody())),
                result.requestSha256());
    }

    @Test
    void attachesFreshReadOnlyFirmwareGrantAndProjectsTypedService()
            throws Exception {
        ObjectNode frozen = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "start-mcu-firmware-update.command.json")));
        frozen.putNull("cosGrant");
        String envelope = objectMapper.writeValueAsString(frozen);
        UUID commandUid = UUID.fromString(
                "8c000000-0000-4000-8000-000000000003");
        Instant grantExpiry = Instant.now().plusSeconds(1800);
        when(cosUploadCredentialPort.issue(
                eq(HARDWARE_SN),
                isNull(),
                eq("ecobin/mcu-firmware/"
                        + "8c000000-0000-4000-8000-000000000001/")))
                .thenReturn(new CosUploadCredential(
                        "TMP_FIRMWARE_ID",
                        "TMP_FIRMWARE_KEY",
                        "FIRMWARE_SESSION_TOKEN",
                        Instant.now().getEpochSecond(),
                        grantExpiry.getEpochSecond(),
                        "ecobin-contract-1250000000",
                        "ap-guangzhou",
                        "https://ecobin-contract-1250000000"
                                + ".cos.ap-guangzhou.myqcloud.com"));
        when(restTemplate.postForEntity(
                anyString(), any(HttpEntity.class), eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(submission(
                envelope,
                commandUid,
                "START_MCU_FIRMWARE_UPDATE"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        assertEquals(
                "startMcuFirmwareUpdate",
                actual.path("identifier").asText());
        JsonNode params = actual.path("params");
        JsonNode first = params.path("scalarFields1");
        JsonNode second = params.path("scalarFields2");
        assertEquals(
                "8c000000-0000-4000-8000-000000000002",
                first.path("deploymentUid").asText());
        assertEquals("2.1.0", first.path("firmwareVersion").asText());
        assertEquals(20100, first.path("firmwareVersionCode").asLong());
        assertEquals("TMP_FIRMWARE_ID",
                first.path("cosGrantTmpSecretId").asText());
        assertEquals("TMP_FIRMWARE_KEY",
                second.path("cosGrantTmpSecretKey").asText());
        assertEquals(
                "ecobin/mcu-firmware/"
                        + "8c000000-0000-4000-8000-000000000001/",
                second.path("cosGrantKeyPrefix").asText());
        assertEquals(
                "FIRMWARE_SESSION_TOKEN",
                params.path("cosGrantSessionTokenParts").get(0).asText());
        Instant issuedAt = Instant.parse(first.path("issuedAt").asText());
        Instant expiresAt = Instant.parse(first.path("expiresAt").asText());
        Instant projectedGrantExpiry = Instant.parse(
                second.path("cosGrantExpiresAt").asText());
        assertTrue(expiresAt.isAfter(issuedAt));
        assertTrue(!expiresAt.isAfter(issuedAt.plusSeconds(900)));
        assertTrue(!projectedGrantExpiry.isBefore(expiresAt));
    }

    @Test
    void rejectsEnvelopeWhoseStableCommandIdentityDiffers()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "apply-configuration.command.json"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(envelope, UUID.randomUUID()));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals(
                "COMMAND_PROJECTION_INVALID",
                result.externalErrorCode());
        verify(restTemplate, never()).postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class));
    }

    @Test
    void projectsFrozenEdgeConfirmationToGeneratedWireContract()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "confirm-edge-event.service-wire.json")));
        ObjectNode expected = (ObjectNode) wireExample
                .path("callServiceApiBodyTemplate")
                .deepCopy();
        expected.put("product_id", PRODUCT_ID);
        expected.put("device_name", HARDWARE_SN);
        assertEquals(expected, actual);
    }

    @Test
    void projectsFrozenLegacyBaselineConfirmationAsUpdated()
            throws Exception {
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "confirm-edge-event.command.json")));
        ObjectNode payload = (ObjectNode) envelope.path("payload");
        payload.put(
                "confirmationUid",
                "c885acf7-3b93-4f84-bd37-35e7c94778e2");
        payload.put(
                "originalEventUid",
                "7762acb9-99ef-4f61-804a-698faa109a29");
        payload.put(
                "originalPayloadSha256",
                "01c8a5a16cad2e4b1f4b075ee5a8782cae0e284149dcb67559aba27a89f844d4");
        payload.put("outcome", "BUSINESS_APPLIED");
        payload.put("effectKind", "BASELINE_ESTABLISHED");
        payload.put("processedAt", "2026-08-09T12:36:04.631Z");
        payload.putArray("resultReferences");
        payload.putNull("errorCode");
        payload.putNull("quarantineUid");
        ((ObjectNode) envelope.path("target")).put(
                "uid", "7762acb9-99ef-4f61-804a-698faa109a29");
        envelope.put(
                "payloadSha256",
                "5dcf48d3ce2dcf7b2e14dd336e1103ed04896a9670387206e2b9dafc5956cd0b");
        UUID commandUid = UUID.fromString(
                "eee42674-699a-4fc4-b857-f79fcec219f5");
        envelope.put("commandUid", commandUid.toString());
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        objectMapper.writeValueAsString(envelope),
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        assertEquals(
                2,
                actual.path("params")
                        .path("scalarFields")
                        .path("effectKind")
                        .asInt());
        assertEquals(
                "2de52464d2ba2976ac075e16299c6c55ffe87a483c0771db137912bd746ca8cb",
                actual.path("params")
                        .path("scalarFields")
                        .path("payloadSha256")
                        .asText());
    }

    @Test
    void projectsPortFullnessStateConfirmationReference()
            throws Exception {
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "confirm-edge-event.command.json")));
        ((ObjectNode) envelope.path("payload")
                .path("resultReferences").get(0))
                .put("type", "PORT_FULLNESS_STATE")
                .put("key", "8498e540-3ba6-43ce-b00f-1cef766de064");
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        objectMapper.writeValueAsString(envelope),
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        assertEquals(
                8,
                actual.path("params")
                        .path("resultReferences")
                        .get(0)
                        .path("type")
                        .asInt());
    }

    @Test
    void exposesSanitizedOneNetBusinessErrorCode() throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok(
                        "{\"code\":10410,\"msg\":\"sensitive detail\"}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.TARGET_NOT_FOUND,
                result.outcome());
        assertEquals(200, result.httpStatus());
        assertEquals("ONENET_10410", result.externalErrorCode());
    }

    @Test
    void classifiesOfflineWithoutTreatingItAsGenericRetryFailure()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok(
                        "{\"code\":10421,\"msg\":\"device offline\"}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.TARGET_OFFLINE,
                result.outcome());
        assertEquals(200, result.httpStatus());
        assertEquals("ONENET_10421", result.externalErrorCode());
    }

    @Test
    void treatsDeterministicServiceCallFailureAsPermanent() throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok(
                        """
                                {"code":10415,
                                 "msg":"required value; access_token=DO_NOT_STORE; https://example.test/a?token=VISIBLE",
                                 "request_id":"a25087f46df04b69b29e90ef0acfd115"}
                                """));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals("ONENET_10415", result.externalErrorCode());
        assertEquals(
                "a25087f46df04b69b29e90ef0acfd115",
                result.externalRequestId());
        assertTrue(result.redactedDiagnostic().contains("required value"));
        assertTrue(result.redactedDiagnostic().contains("<redacted>"));
        assertFalse(result.redactedDiagnostic().contains("DO_NOT_STORE"));
        assertFalse(result.redactedDiagnostic().contains("VISIBLE"));
        assertTrue(result.redactedDiagnostic().length() <= 1000);
    }

    @Test
    void acceptsCamelCaseRequestIdButDropsUnsafeRequestIdentity()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(
                        ResponseEntity.ok(
                                """
                                        {"code":10415,"msg":"bad model",
                                         "requestId":"camelCase-req_01"}
                                        """),
                        ResponseEntity.ok(
                                """
                                        {"code":10415,"msg":"bad model",
                                         "request_id":"unsafe request id"}
                                        """));

        DeviceCommandSubmissionResult camelCase = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));
        DeviceCommandSubmissionResult unsafe = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                camelCase.outcome());
        assertEquals("camelCase-req_01", camelCase.externalRequestId());
        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                unsafe.outcome());
        assertNull(unsafe.externalRequestId());
    }

    @Test
    void boundsAnOversizedOneNetMessageAfterSanitization()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok(
                        objectMapper.writeValueAsString(Map.of(
                                "code", 10415,
                                "msg", "x".repeat(2_000),
                                "request_id",
                                "long-message-request-01"))));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        UUID.fromString(
                                "60000000-0000-4000-8000-000000000002"),
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals("ONENET_10415", result.externalErrorCode());
        assertEquals(
                "long-message-request-01",
                result.externalRequestId());
        assertTrue(result.redactedDiagnostic().length() <= 1000);
        assertTrue(result.redactedDiagnostic().contains("<truncated"));
    }

    @Test
    void keepsExplicitInternalServiceErrorRetryable() throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "confirm-edge-event.command.json"));
        UUID commandUid = UUID.fromString(
                "60000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok(
                        """
                                {"code":10500,
                                 "msg":"internal service error",
                                 "requestId":"temporary-failure-req-01"}
                                """));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "CONFIRM_EDGE_EVENT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.RETRYABLE_FAILURE,
                result.outcome());
        assertEquals("ONENET_10500", result.externalErrorCode());
        assertEquals(
                "temporary-failure-req-01",
                result.externalRequestId());
        assertTrue(result.redactedDiagnostic().contains(
                "internal service error"));
    }

    @Test
    void projectsFrozenDeliverySessionToGeneratedWireContract()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "start-delivery-session.command.json"));
        UUID commandUid = UUID.fromString(
                "30000000-0000-4000-8000-000000000003");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "START_DELIVERY_SESSION"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "start-delivery-session"
                                + ".service-wire.json")));
        ObjectNode expected = (ObjectNode) wireExample
                .path("callServiceApiBodyTemplate")
                .deepCopy();
        expected.put("product_id", PRODUCT_ID);
        expected.put("device_name", HARDWARE_SN);
        expectInitialDeliveryCosGrant(actual, expected);
        assertEquals(expected, actual);
    }

    @Test
    void projectsFrozenCleanOperationToGeneratedWireContract()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "start-clean-operation.command.json"));
        UUID commandUid = UUID.fromString(
                "81000000-0000-4000-8000-000000000001");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "START_CLEAN_OPERATION"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "start-clean-operation"
                                + ".service-wire.json")));
        ObjectNode expected = (ObjectNode) wireExample
                .path("callServiceApiBodyTemplate")
                .deepCopy();
        expected.put("product_id", PRODUCT_ID);
        expected.put("device_name", HARDWARE_SN);
        expectInitialCleanCosGrant(actual, expected);
        assertEquals(expected, actual);
    }

    @Test
    void projectsFrozenFullnessSampleToGeneratedWireContract()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "sample-fullness.command.json"));
        UUID commandUid = UUID.fromString(
                "81000000-0000-4000-8000-000000000004");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "SAMPLE_FULLNESS"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "sample-fullness"
                                + ".service-wire.json")));
        ObjectNode expected = (ObjectNode) wireExample
                .path("callServiceApiBodyTemplate")
                .deepCopy();
        expected.put("product_id", PRODUCT_ID);
        expected.put("device_name", HARDWARE_SN);
        assertEquals(expected, actual);
        verify(cosUploadCredentialPort, never()).issue(
                anyString(),
                eq(1),
                anyString());
    }

    @Test
    void rejectsDeliveryTargetThatDiffersFromPayloadSession()
            throws Exception {
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "start-delivery-session.command.json")));
        ((ObjectNode) envelope.path("target")).put(
                "uid",
                "30000000-0000-4000-8000-000000000099");

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        objectMapper.writeValueAsString(envelope),
                        UUID.fromString(
                                "30000000-0000-4000-8000-000000000003"),
                        "START_DELIVERY_SESSION"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                result.outcome());
        assertEquals(
                "COMMAND_PROJECTION_INVALID",
                result.externalErrorCode());
        verify(restTemplate, never()).postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class));
    }

    @Test
    void signsPhotoGrantOnlyWhenProjectingOutboundCall()
            throws Exception {
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "provide-photo-upload-grant"
                                + ".command.json")));
        envelope.set("cosGrant", null);
        UUID commandUid = UUID.fromString(
                "83000000-0000-4000-8000-000000000002");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        objectMapper.writeValueAsString(envelope),
                        commandUid,
                        "PROVIDE_PHOTO_UPLOAD_GRANT"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode params = actual.path("params");
        assertEquals(
                "providePhotoUploadGrant",
                actual.path("identifier").asText());
        assertEquals(
                "TMP_SECRET_ID",
                params.path("scalarFields")
                        .path("cosGrantTmpSecretId")
                        .asText());
        assertEquals(
                "ecobin/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/",
                params.path("scalarFields")
                        .path("cosGrantKeyPrefix")
                        .asText());
        assertEquals(
                4,
                params.path("authorizedSlots").size());
        verify(cosUploadCredentialPort).issue(
                HARDWARE_SN,
                1,
                "ecobin/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
    }

    @Test
    void signsAcceptanceGrantWithItsDedicatedCosPrefix()
            throws Exception {
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "request-device-acceptance.command.json")));
        envelope.set("cosGrant", null);
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000004");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        objectMapper.writeValueAsString(envelope),
                        commandUid,
                        "REQUEST_DEVICE_ACCEPTANCE"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode params = actual.path("params");
        assertEquals(
                "requestDeviceAcceptance",
                actual.path("identifier").asText());
        assertEquals(4, params.size());
        assertFalse(params.has("scalarFields"));
        assertEquals(
                1,
                params.path("scalarFields1")
                        .path("schemaVersion")
                        .asInt());
        assertEquals(
                HARDWARE_SN,
                params.path("scalarFields1")
                        .path("targetDeviceName")
                        .asText());
        assertEquals(
                2,
                params.path("scalarFields1")
                        .path("factoryBagRevision")
                        .asLong());
        assertEquals(
                "a".repeat(64),
                params.path("scalarFields1")
                        .path("factoryBagSetSha256")
                        .asText());
        assertEquals(
                "ap-guangzhou",
                params.path("scalarFields1")
                        .path("cosGrantRegion")
                        .asText());
        assertEquals(
                "ecobin/device-acceptance/"
                        + "8a000000-0000-4000-8000-000000000003/",
                params.path("scalarFields2")
                        .path("cosGrantKeyPrefix")
                        .asText());
        assertEquals(
                HARDWARE_SN,
                params.path("target").path("uid").asText());
        assertEquals(1, params.path("cosGrantSessionTokenParts").size());
        verify(cosUploadCredentialPort).issue(
                HARDWARE_SN,
                null,
                "ecobin/device-acceptance/"
                        + "8a000000-0000-4000-8000-000000000003/");
    }

    @Test
    void projectsFactorySealAuthorizationWithoutCosCredentials()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "authorize-factory-seal.command.json"));
        JsonNode semanticPayload = objectMapper.readTree(envelope)
                .path("payload");
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000007");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "AUTHORIZE_FACTORY_SEAL"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        JsonNode params = actual.path("params");
        assertEquals(
                "authorizeFactorySeal",
                actual.path("identifier").asText());
        assertEquals(HARDWARE_SN, params.path("hardwareSn").asText());
        assertEquals(1L, params.path("acceptanceGeneration").asLong());
        assertEquals(2L, params.path("factoryBagRevision").asLong());
        assertEquals(
                semanticPayload.path("acceptanceEvidenceSha256").asText(),
                params.path("acceptanceEvidenceSha256").asText());
        assertEquals(
                semanticPayload.path("factoryBagSetSha256").asText(),
                params.path("factoryBagSetSha256").asText());
        assertFalse(params.path("cosGrantPresent").asBoolean());
        verify(cosUploadCredentialPort, never()).issue(
                anyString(), any(), anyString());
    }

    @Test
    void projectsDeviceEntryUrlSyncWithoutCreatingCosCredentials()
            throws Exception {
        String envelope = Files.readString(contractPath(
                "contracts/examples/onenet/"
                        + "sync-device-entry-url.command.json"));
        UUID commandUid = UUID.fromString(
                "8a000000-0000-4000-8000-000000000005");
        when(restTemplate.postForEntity(
                anyString(),
                any(HttpEntity.class),
                eq(String.class)))
                .thenReturn(ResponseEntity.ok("{\"code\":0}"));

        DeviceCommandSubmissionResult result = client.submit(
                submission(
                        envelope,
                        commandUid,
                        "SYNC_DEVICE_ENTRY_URL"));

        assertEquals(
                DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                result.outcome());
        @SuppressWarnings("rawtypes")
        ArgumentCaptor<HttpEntity> request =
                ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).postForEntity(
                anyString(), request.capture(), eq(String.class));
        JsonNode actual = objectMapper.valueToTree(
                request.getValue().getBody());
        assertEquals(
                "syncDeviceEntryUrl",
                actual.path("identifier").asText());
        assertEquals(
                "https://www.jinshoubao.com/device-entry/"
                        + "?deviceCode="
                        + "Dv_contract000000000000000000000000",
                actual.path("params").path("deviceEntryUrl").asText());
        assertEquals(
                false,
                actual.path("params").path("cosGrantPresent")
                        .asBoolean());
    }

    private DeviceCommandSubmission submission(
            String envelope,
            UUID commandUid) throws Exception {
        return submission(
                envelope,
                commandUid,
                "APPLY_CONFIGURATION");
    }

    private void expectInitialDeliveryCosGrant(
            JsonNode actual,
            ObjectNode expected) {
        JsonNode actualParams = actual.path("params");
        ObjectNode expectedScalar1 =
                (ObjectNode) expected.path("params")
                        .path("scalarFields1");
        expectedScalar1.put("cosGrantPresent", true);
        expectedScalar1.put(
                "cosGrantGrantUid",
                actualParams.path("scalarFields1")
                        .path("cosGrantGrantUid")
                        .asText());
        expectedScalar1.put(
                "cosGrantTmpSecretId",
                "TMP_SECRET_ID");
        expectedScalar1.put(
                "cosGrantTmpSecretKey",
                "TMP_SECRET_KEY");
        expectedScalar1.put(
                "cosGrantBucket",
                "ecobin-contract-1250000000");
        ObjectNode expectedScalar2 =
                (ObjectNode) expected.path("params")
                        .path("scalarFields2");
        expectedScalar2.put(
                "cosGrantRegion",
                "ap-guangzhou");
        expectedScalar2.put(
                "cosGrantBaseUrl",
                "https://ecobin-contract-1250000000"
                        + ".cos.ap-guangzhou.myqcloud.com");
        expectedScalar2.put(
                "cosGrantKeyPrefix",
                "ecobin/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
        expectedScalar2.put(
                "cosGrantExpiresAt",
                "2026-07-24T01:30:00Z");
        ((ObjectNode) expected.path("params")).putArray(
                        "cosGrantSessionTokenParts")
                .add("SESSION_TOKEN");
        verify(cosUploadCredentialPort).issue(
                HARDWARE_SN,
                2,
                "ecobin/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
    }

    private void expectInitialCleanCosGrant(
            JsonNode actual,
            ObjectNode expected) {
        JsonNode actualParams = actual.path("params");
        ObjectNode expectedScalar1 =
                (ObjectNode) expected.path("params")
                        .path("scalarFields1");
        expectedScalar1.put("cosGrantPresent", true);
        expectedScalar1.put(
                "cosGrantGrantUid",
                actualParams.path("scalarFields1")
                        .path("cosGrantGrantUid")
                        .asText());
        expectedScalar1.put("cosGrantTmpSecretId", "TMP_SECRET_ID");
        ObjectNode expectedScalar2 =
                (ObjectNode) expected.path("params")
                        .path("scalarFields2");
        expectedScalar2.put("cosGrantTmpSecretKey", "TMP_SECRET_KEY");
        expectedScalar2.put(
                "cosGrantExpiresAt",
                "2026-07-24T01:30:00Z");
        ((ObjectNode) expected.path("params")).putArray(
                        "cosGrantSessionTokenParts")
                .add("SESSION_TOKEN");
        verify(cosUploadCredentialPort).issue(
                HARDWARE_SN,
                2,
                "ecobin/clean-operation/"
                        + "40000000-0000-4000-8000-000000000001/");
    }

    private DeviceCommandSubmission submission(
            String envelope,
            UUID commandUid,
            String commandType) throws Exception {
        byte[] bytes = envelope.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        return new DeviceCommandSubmission(
                TASK_UID,
                commandUid,
                commandType,
                HARDWARE_SN,
                envelope,
                MessageDigest.getInstance("SHA-256").digest(bytes));
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
}
