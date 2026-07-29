package org.enveloping.ecobin.integration.onenet.outbound;

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
    private OneNetClient client;

    @BeforeEach
    void setUp() {
        OneNetProperties properties = new OneNetProperties();
        properties.setBaseUrl("https://onenet.invalid");
        properties.setInvokeServicePath("/thingmodel/call-service");
        properties.setProductId(PRODUCT_ID);
        properties.setAccessKey("c2FtcGxlLWtleQ==");
        restTemplate = mock(RestTemplate.class);
        client = new OneNetClient(
                properties,
                restTemplate,
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

    private DeviceCommandSubmission submission(
            String envelope,
            UUID commandUid) throws Exception {
        byte[] bytes = envelope.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        return new DeviceCommandSubmission(
                TASK_UID,
                commandUid,
                "APPLY_CONFIGURATION",
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
