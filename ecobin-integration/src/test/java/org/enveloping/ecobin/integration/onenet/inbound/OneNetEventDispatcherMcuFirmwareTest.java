package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherMcuFirmwareTest {

    private static final String PRODUCT_ID = "ecobin-product-contract";
    private static final String HARDWARE_SN = "SN-CONTRACT-0001";

    private TrustedInboxPort inboxPort;
    private TrustedDeviceSourceScopePort sourceScopePort;
    private ObjectMapper objectMapper;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        inboxPort = mock(TrustedInboxPort.class);
        sourceScopePort = mock(TrustedDeviceSourceScopePort.class);
        objectMapper = JsonMapper.builder().build();
        OneNetProperties properties = new OneNetProperties();
        properties.setProductId(PRODUCT_ID);
        when(sourceScopePort.resolverForPlatformAsset(HARDWARE_SN))
                .thenReturn(writer -> writer.platform());
        when(inboxPort.receive(any())).thenReturn(
                new TrustedInboxReceipt(
                        TrustedInboxReceiptState.ACCEPTED,
                        UUID.randomUUID(),
                        UUID.randomUUID(),
                        null,
                        "a".repeat(64),
                        true));
        dispatcher = new OneNetEventDispatcher(
                inboxPort,
                sourceScopePort,
                properties,
                new CosProperties(),
                objectMapper);
    }

    @Test
    void firmwareProgressIsNormalizedAndKeptInPlatformScope()
            throws Exception {
        dispatcher.handle(
                decrypted(wireValue()),
                "mq-mcu-firmware-progress",
                "encrypted-mcu-firmware-progress"
                        .getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "onenet.mcu-firmware-progress",
                message.sourceNamespace());
        assertEquals(
                "MCU_FIRMWARE_UPDATE_PROGRESS",
                message.messageKind());
        JsonNode normalized = objectMapper.readTree(
                message.normalizedPayload());
        JsonNode event = normalized.path("event");
        assertEquals(
                "8c000000-0000-4000-8000-000000000002",
                event.path("target").path("uid").asText());
        assertEquals(
                "SUCCEEDED",
                event.path("payload").path("stage").asText());
        assertEquals(
                "0123456789abcdef",
                event.path("payload")
                        .path("installedFirmwareIdentityHex").asText());
        assertEquals(1,
                event.path("payload").path("targetAttemptCount").asInt());

        AtomicReference<String> kind = new AtomicReference<>();
        AtomicReference<Long> tenantId = new AtomicReference<>();
        AtomicReference<Long> organizationId = new AtomicReference<>();
        message.scopeResolver().resolve((scope, tenant, organization) -> {
            kind.set(scope);
            tenantId.set(tenant);
            organizationId.set(organization);
        });
        assertEquals("PLATFORM", kind.get());
        assertNull(tenantId.get());
        assertNull(organizationId.get());
    }

    @Test
    void packageFetchFailureCodeTwelveIsNormalizedWithItsError()
            throws Exception {
        ObjectNode wire = failureBeforeFlashWire(
                12, "PACKAGE_FETCH_FAILED", "COS_DOWNLOAD_FAILED");

        dispatcher.handle(
                decrypted(wire),
                "mq-mcu-firmware-package-fetch-failed",
                "encrypted-mcu-firmware-package-fetch-failed"
                        .getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode payload = objectMapper.readTree(
                captor.getValue().normalizedPayload())
                .path("event")
                .path("payload");
        assertEquals("PACKAGE_FETCH_FAILED", payload.path("stage").asText());
        assertEquals("COS_DOWNLOAD_FAILED", payload.path("errorCode").asText());
        assertEquals(0, payload.path("targetAttemptCount").asInt());
    }

    @Test
    void unavailableRemoteUpdateIsNormalizedAsStableRejection()
            throws Exception {
        ObjectNode wire = failureBeforeFlashWire(
                11, "REJECTED", "MCU_REMOTE_UPDATE_UNAVAILABLE");

        dispatcher.handle(
                decrypted(wire),
                "mq-mcu-firmware-remote-update-unavailable",
                "encrypted-mcu-firmware-remote-update-unavailable"
                        .getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode payload = objectMapper.readTree(
                        captor.getValue().normalizedPayload())
                .path("event")
                .path("payload");
        assertEquals("REJECTED", payload.path("stage").asText());
        assertEquals(
                "MCU_REMOTE_UPDATE_UNAVAILABLE",
                payload.path("errorCode").asText());
        assertEquals(0, payload.path("targetAttemptCount").asInt());
        assertEquals(0, payload.path("rollbackAttemptCount").asInt());
    }

    private ObjectNode failureBeforeFlashWire(
            int wireStage,
            String semanticStage,
            String errorCode) throws Exception {
        ObjectNode wire = wireValue();
        wire.put("stage", wireStage);
        wire.put("targetAttemptCount", 0);
        wire.put("installedFirmwareVersionPresent", false);
        wire.put("installedFirmwareVersion", "");
        wire.put("installedFirmwareVersionCPresent", false);
        wire.put("installedFirmwareVersionC", 0);
        wire.put("installedFirmwareIdentityPresent", false);
        wire.put("installedFirmwareIdentity", "");
        wire.put("errorCodePresent", true);
        wire.put("errorCode", errorCode);
        Map<String, Object> semanticPayload = new LinkedHashMap<>();
        semanticPayload.put(
                "deploymentUid",
                "8c000000-0000-4000-8000-000000000002");
        semanticPayload.put(
                "updateUid",
                "8c000000-0000-4000-8000-000000000004");
        semanticPayload.put(
                "releaseUid",
                "8c000000-0000-4000-8000-000000000001");
        semanticPayload.put("source", "CLOUD");
        semanticPayload.put("stage", semanticStage);
        semanticPayload.put("firmwareVersion", "2.1.0");
        semanticPayload.put("firmwareVersionCode", 20_100L);
        semanticPayload.put("firmwareIdentityHex", "0123456789abcdef");
        semanticPayload.put("fixedFrameRevision", 2L);
        semanticPayload.put("targetAttemptCount", 0L);
        semanticPayload.put("rollbackAttemptCount", 0L);
        semanticPayload.put("legacyPreflight", false);
        semanticPayload.put("downgradeAuthorized", false);
        semanticPayload.put("installedFirmwareVersion", null);
        semanticPayload.put("installedFirmwareVersionCode", null);
        semanticPayload.put("installedFirmwareIdentityHex", null);
        semanticPayload.put("errorCode", errorCode);
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(semanticPayload));
        return wire;
    }

    private String decrypted(ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        subData.putObject("params")
                .putObject("mcuFirmwareUpdateProgress")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode wireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "mcu-firmware-update-progress.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("mcuFirmwareUpdateProgress")
                .path("value")
                .deepCopy();
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("").toAbsolutePath().normalize();
        Path repository = Files.isDirectory(workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }
}
