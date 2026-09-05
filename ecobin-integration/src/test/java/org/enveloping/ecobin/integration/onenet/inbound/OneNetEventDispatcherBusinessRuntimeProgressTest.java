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
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherBusinessRuntimeProgressTest {

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
    void successfulBusinessUpdateProgressIsNormalizedInPlatformScope()
            throws Exception {
        dispatcher.handle(
                decrypted(wireValue()),
                "mq-business-runtime-progress",
                "encrypted-business-runtime-progress"
                        .getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "onenet.business-runtime-progress",
                message.sourceNamespace());
        assertEquals(
                "BUSINESS_RUNTIME_UPDATE_PROGRESS",
                message.messageKind());
        JsonNode event = objectMapper.readTree(message.normalizedPayload())
                .path("event");
        assertEquals(
                "8e000000-0000-4000-8000-000000000002",
                event.path("target").path("uid").asText());
        assertEquals(
                "8e000000-0000-4000-8000-000000000003",
                event.path("commandUid").asText());
        JsonNode payload = event.path("payload");
        assertEquals("SUCCEEDED", payload.path("stage").asText());
        assertEquals(
                "1.1.0-rc.1",
                payload.path("installedVersionName").asText());
        assertEquals(13,
                payload.path("installedReleaseSequence").asLong());
        assertEquals(1,
                payload.path("downloadAttemptCount").asInt());

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
    void cancellationResultIsNormalizedAsASeparatePlatformFact()
            throws Exception {
        dispatcher.handle(
                decryptedCancellation(cancellationWireValue()),
                "mq-business-runtime-cancellation",
                "encrypted-business-runtime-cancellation"
                        .getBytes(StandardCharsets.UTF_8));

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "onenet.business-runtime-cancel-result",
                message.sourceNamespace());
        assertEquals(
                "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT",
                message.messageKind());
        JsonNode event = objectMapper.readTree(message.normalizedPayload())
                .path("event");
        assertEquals(
                "8e000000-0000-4000-8000-000000000006",
                event.path("commandUid").asText());
        JsonNode payload = event.path("payload");
        assertEquals("CANCELLED", payload.path("result").asText());
        assertEquals(
                "WAITING_FOR_IDLE",
                payload.path("observedStage").asText());
        assertEquals("OPEN", payload.path("businessAdmissionState").asText());
        assertEquals(2, payload.path("controlSequence").asLong());
        assertEquals(true, payload.path("errorCode").isNull());
    }

    private String decrypted(ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        subData.putObject("params")
                .putObject("businessRuntimeUpdateProgress")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode wireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "business-runtime-update-progress.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("businessRuntimeUpdateProgress")
                .path("value")
                .deepCopy();
    }

    private String decryptedCancellation(ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        subData.putObject("params")
                .putObject("businessRuntimeUpdateCancelResult")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode cancellationWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "business-runtime-update-cancel-result.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("businessRuntimeUpdateCancelResult")
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
