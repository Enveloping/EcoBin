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
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherAcceptanceTest {

    private static final String PRODUCT_ID = "ecobin-product-contract";
    private static final String HARDWARE_SN = "SN-CONTRACT-0001";
    private static final String CONFIRMATION_UID =
            "60000000-0000-4000-8000-000000000001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-acceptance-transport-evidence"
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
        TrustedInboxScopeResolver resolver = writer -> writer.platform();
        when(sourceScopePort.resolverForPlatformAsset(HARDWARE_SN))
                .thenReturn(resolver);
        when(sourceScopePort.resolverForBusinessConfirmation(
                HARDWARE_SN, CONFIRMATION_UID)).thenReturn(resolver);
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
    void acceptanceEvidenceConvertsOneNetEnumAndUsesPlatformScope()
            throws Exception {
        dispatcher.handle(
                decrypted(
                        "deviceAcceptanceEvidence",
                        acceptanceWireValue()),
                "mq-device-acceptance",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        TrustedInboxMessage message = captor.getValue();
        assertEquals("DEVICE_ACCEPTANCE_EVIDENCE", message.messageKind());

        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet/"
                                + "device-acceptance-evidence.event.json")));
        assertEquals(canonicalHash(expected), canonicalHash(normalized));
        assertEquals("2", normalized.path("payload")
                .path("edgeProtocolVersion").asText());
    }

    @Test
    void deviceEntryUrlApplicationUsesPlatformScopeAndPreservesDisplayBasis()
            throws Exception {
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-entry-url-application-result.event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceEntryUrlApplicationResult")
                .path("value")
                .deepCopy();

        dispatcher.handle(
                decrypted("deviceEntryUrlApplicationResult", wire),
                "mq-device-entry-url-application",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "DEVICE_ENTRY_URL_APPLICATION_RESULT",
                message.messageKind());
        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet/"
                                + "device-entry-url-application-result.event.json")));
        assertEquals(canonicalHash(expected), canonicalHash(normalized));
        assertEquals(
                "UART3_COMMAND_ATOMICALLY_QUEUED",
                normalized.path("payload").path("displayBasis").asText());
    }

    @Test
    void legacyV3AcceptanceWithoutNewWireMembersRemainsAccepted()
            throws Exception {
        dispatcher.handle(
                decrypted(
                        "deviceAcceptanceEvidence",
                        acceptanceWireValueV3()),
                "mq-device-acceptance-v3",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode payload = objectMapper.readTree(
                        captor.getValue().normalizedPayload())
                .path("event")
                .path("payload");
        assertEquals(3, payload.path("evidenceSchemaVersion").asInt());
        assertFalse(payload.has("mcuRemoteUpdateCapable"));
    }

    @Test
    void v4AcceptanceRequiresExplicitCapabilityPresence()
            throws Exception {
        ObjectNode wire = acceptanceWireValue();
        wire.remove("mcuRemoteUpdateCapablePresent");

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted("deviceAcceptanceEvidence", wire),
                        "mq-device-acceptance-v4-missing-capability",
                        RAW_TRANSPORT));
    }

    @Test
    void confirmationReceiptResolvesScopeFromItsFrozenTask()
            throws Exception {
        dispatcher.handle(
                decrypted(
                        "businessConfirmationReceipt",
                        confirmationReceiptWireValue()),
                "mq-confirmation-receipt",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        verify(sourceScopePort).resolverForBusinessConfirmation(
                HARDWARE_SN, CONFIRMATION_UID);
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "BUSINESS_CONFIRMATION_RECEIPT",
                message.messageKind());

        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet/"
                                + "business-confirmation-receipt.event.json")));
        assertEquals(canonicalHash(expected), canonicalHash(normalized));
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

    private ObjectNode confirmationReceiptWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "business-confirmation-receipt.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("businessConfirmationReceipt")
                .path("value")
                .deepCopy();
    }

    private ObjectNode acceptanceWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-acceptance-evidence.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("deviceAcceptanceEvidence")
                .path("value")
                .deepCopy();
    }

    private ObjectNode acceptanceWireValueV3() throws Exception {
        ObjectNode wire = acceptanceWireValue();
        wire.put("evidenceSchemaVersion", 1);
        wire.remove("mcuRemoteUpdateCapablePresent");
        wire.remove("mcuRemoteUpdateCapable");
        wire.remove("deviceEntryUrlMcuAppliedPresent");
        wire.remove("deviceEntryUrlMcuApplied");
        wire.remove("deviceEntryUrlAppliedSha2Present");
        wire.remove("deviceEntryUrlAppliedSha2");
        wire.remove("deviceEntryUrlAppliedMcuBPresent");
        wire.remove("deviceEntryUrlAppliedMcuB");
        wire.remove("deviceEntryUrlDisplayBasiPresent");
        wire.remove("deviceEntryUrlDisplayBasi");

        ObjectNode payload = (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet/"
                                        + "device-acceptance-evidence.event.json")))
                .path("payload")
                .deepCopy();
        payload.put("evidenceSchemaVersion", 3);
        payload.remove("mcuRemoteUpdateCapable");
        payload.remove("deviceEntryUrlMcuApplied");
        payload.remove("deviceEntryUrlAppliedSha256");
        payload.remove("deviceEntryUrlAppliedMcuBootId");
        payload.remove("deviceEntryUrlDisplayBasis");
        wire.put("payloadSha256", canonicalHash(payload));
        return wire;
    }

    private String canonicalHash(JsonNode value) {
        @SuppressWarnings("unchecked")
        Map<String, Object> semantic = objectMapper.convertValue(value, Map.class);
        return OneNetCanonicalJson.payloadSha256(semantic);
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("").toAbsolutePath().normalize();
        Path repository = Files.isDirectory(workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }
}
