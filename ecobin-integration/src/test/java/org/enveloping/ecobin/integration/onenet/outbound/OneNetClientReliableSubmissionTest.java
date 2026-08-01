package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
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
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetClientReliableSubmissionTest {

    private static final UUID TASK_UID =
            UUID.fromString("30000000-0000-4000-8000-000000000001");
    private static final UUID COMMAND_UID =
            UUID.fromString("20000000-0000-4000-8000-000000000001");
    private static final String HARDWARE_SN = "HW-CONTRACT-001";
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
        client = new OneNetClient(
                properties,
                restTemplate,
                cosUploadCredentialPort,
                objectMapper);
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
                "ecobin/Dp_demo_01/delivery-session/"
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
                "ecobin/Dp_demo_01/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
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
                "ecobin/Dp_demo_01/delivery-session/"
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
                "ecobin/Dp_demo_01/delivery-session/"
                        + "30000000-0000-4000-8000-000000000001/");
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
