package org.enveloping.ecobin.integration.onenet.inbound;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceiptState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
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

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherCommandObservedTest {

    private static final String PRODUCT_ID =
            "ecobin-product-contract";
    private static final String HARDWARE_SN =
            "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-command-transport-evidence"
                    .getBytes(StandardCharsets.UTF_8);

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
        CosProperties cosProperties = new CosProperties();
        TrustedInboxScopeResolver resolver =
                writer -> writer.organization(11, 22);
        when(sourceScopePort.resolverForOrganizationAsset(HARDWARE_SN))
                .thenReturn(resolver);
        when(sourceScopePort.resolverForPlatformAsset(HARDWARE_SN))
                .thenReturn(TrustedInboxScopeResolver.platform());
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
                cosProperties,
                objectMapper);
    }

    @Test
    void hardwareCommandObservationNormalizesToSemanticContract()
            throws Exception {
        ObjectNode wire = commandWireValue();

        dispatcher.handle(
                decrypted(wire),
                "mq-command-observed",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "DEVICE_COMMAND_OBSERVED",
                message.messageKind());
        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-command-observed"
                                + ".event.json")));
        assertEquals(
                canonicalHash(expected),
                canonicalHash(normalized));
    }

    @Test
    void commandTargetMustEqualEnvelopeCommand()
            throws Exception {
        ObjectNode wire = commandWireValue();
        ((ObjectNode) wire.path("target")).put(
                "uid",
                "30000000-0000-4000-8000-000000000099");

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(wire),
                        "mq-invalid-command",
                        RAW_TRANSPORT));
        verify(inboxPort, never()).receive(any());
        verify(inboxPort).quarantine(any());
    }

    @Test
    void factorySealObservationUsesPlatformAssetAuthority()
            throws Exception {
        ObjectNode wire = commandWireValue();
        wire.put("observedCommandType", 7);
        wire.put("stage", 1);
        wire.put("mcuCommandUidPresent", false);
        wire.put("errorCodePresent", false);
        wire.remove("mcuCommandUid");
        wire.remove("errorCode");
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("observedCommandType", "AUTHORIZE_FACTORY_SEAL");
        payload.put("stage", "RECEIVED");
        payload.put("mcuCommandUid", null);
        payload.put("errorCode", null);
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(payload));

        dispatcher.handle(
                decrypted(wire),
                "mq-factory-seal-observed",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "onenet.factory-seal-observation",
                message.sourceNamespace());
        assertEquals(
                "AUTHORIZE_FACTORY_SEAL",
                objectMapper.readTree(message.normalizedPayload())
                        .path("event")
                        .path("payload")
                        .path("observedCommandType")
                        .asText());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        verify(sourceScopePort, never())
                .resolverForOrganizationAsset(HARDWARE_SN);
    }

    @Test
    void factorySealCompletionNormalizesToPlatformAssetFact()
            throws Exception {
        ObjectNode wire = eventWireValue(
                "factory-seal-completed", "factorySealCompleted");

        dispatcher.handle(
                decrypted("factorySealCompleted", wire),
                "mq-factory-seal-completed",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals("FACTORY_SEAL_COMPLETED", message.messageKind());
        assertEquals(
                "onenet.factory-seal-completion",
                message.sourceNamespace());
        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "factory-seal-completed.event.json")));
        assertEquals(canonicalHash(expected), canonicalHash(normalized));
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        verify(sourceScopePort, never())
                .resolverForOrganizationAsset(HARDWARE_SN);
    }

    @Test
    void factorySealCompletionRejectsForgedAuthorizationBinding()
            throws Exception {
        ObjectNode wire = eventWireValue(
                "factory-seal-completed", "factorySealCompleted");
        wire.put("authorizationBindingSha256", "0".repeat(64));

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted("factorySealCompleted", wire),
                        "mq-forged-factory-seal-completed",
                        RAW_TRANSPORT));

        verify(inboxPort, never()).receive(any());
        verify(inboxPort).quarantine(any());
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "2026-07-24T01:00:30.001Z",
            "2026-07-24T09:00:30.000Z"
    })
    void factorySealCompletionRejectsEventAfterCleanup(
            String occurredAt) throws Exception {
        ObjectNode wire = eventWireValue(
                "factory-seal-completed", "factorySealCompleted");
        wire.put("occurredAt", occurredAt);

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted("factorySealCompleted", wire),
                        "mq-late-factory-seal-completed",
                        RAW_TRANSPORT));

        verify(inboxPort, never()).receive(any());
        verify(inboxPort).quarantine(any());
    }

    private String canonicalHash(JsonNode value) {
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic =
                objectMapper.convertValue(value, Map.class);
        return OneNetCanonicalJson.payloadSha256(semantic);
    }

    private String decrypted(ObjectNode value) {
        return decrypted("deviceCommandObserved", value);
    }

    private String decrypted(String identifier, ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        subData.putObject("params")
                .putObject(identifier)
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode commandWireValue() throws Exception {
        return eventWireValue(
                "device-command-observed", "deviceCommandObserved");
    }

    private ObjectNode eventWireValue(
            String filename, String identifier) throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + filename
                                + ".event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path(identifier)
                .path("value")
                .deepCopy();
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
