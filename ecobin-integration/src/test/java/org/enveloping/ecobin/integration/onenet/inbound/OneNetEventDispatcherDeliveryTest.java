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
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherDeliveryTest {

    private static final String PRODUCT_ID =
            "ecobin-product-contract";
    private static final String HARDWARE_SN =
            "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-transport-evidence"
                    .getBytes(StandardCharsets.UTF_8);

    private TrustedInboxPort inboxPort;
    private ObjectMapper objectMapper;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        inboxPort = mock(TrustedInboxPort.class);
        TrustedDeviceSourceScopePort sourceScopePort =
                mock(TrustedDeviceSourceScopePort.class);
        objectMapper = JsonMapper.builder().build();
        OneNetProperties properties = new OneNetProperties();
        properties.setProductId(PRODUCT_ID);
        CosProperties cosProperties = new CosProperties();
        cosProperties.setBaseUrl(
                "https://ecobin-contract-1250000000"
                        + ".cos.ap-guangzhou.myqcloud.com");
        TrustedInboxScopeResolver resolver =
                writer -> writer.organization(11, 22);
        when(sourceScopePort.resolverForOrganizationAsset(HARDWARE_SN))
                .thenReturn(resolver);
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
    void deliveryCompleteWireNormalizesToFrozenSemanticExample()
            throws Exception {
        dispatcher.handle(
                decrypted(deliveryWireValue()),
                "mq-delivery-complete",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "DELIVERY_COMPLETE",
                message.messageKind());
        assertEquals(
                "30000000-0000-4000-8000-000000000006",
                message.externalMessageId());

        JsonNode normalized =
                objectMapper.readTree(message.normalizedPayload());
        JsonNode actualEvent = normalized.path("event");
        JsonNode expectedEvent = objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "delivery-complete.event.json")));
        assertEquals(
                canonicalHash(expectedEvent),
                canonicalHash(actualEvent));
        assertEquals(
                expectedEvent.path("payloadSha256").asText(),
                actualEvent.path("payloadSha256").asText());
        assertEquals(
                4,
                actualEvent.path("payload").path("photos").size());
    }

    @Test
    void deliveryTargetMustIdentifyPayloadSession()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ((ObjectNode) wire.path("target")).put(
                "uid",
                "30000000-0000-4000-8000-000000000099");

        assertPermanentlyRejected(wire);
    }

    @Test
    void deliveryMeasurementAvailabilityMustBeConsistent()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ((ObjectNode) wire.path("firstPreOpenMeasurement"))
                .put("weightValueAvailable", false);

        assertPermanentlyRejected(wire);
    }

    @Test
    void stableDeliveryMeasurementRequiresAtLeastOneSample()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ((ObjectNode) wire.path("firstPreOpenMeasurement"))
                .put("sampleCount", 0);

        assertPermanentlyRejected(wire);
    }

    @Test
    void deliveryManualReviewMustMatchCompletionReason()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        wire.put("manualReviewRequired", true);

        assertPermanentlyRejected(wire);
    }

    @Test
    void terminalWeightFailurePreservesNullablePhysicalFacts()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        wire.put("firstPreOpenMeasurementPresent", false);
        wire.remove("firstPreOpenMeasurement");
        wire.put("finalPostCloseMeasurementPresent", false);
        wire.remove("finalPostCloseMeasurement");
        wire.put("deliveryNetWeightGramsPresent", false);
        wire.put("deliveryNetWeightGrams", 0);
        wire.put("finalDoorCommandPresent", false);
        wire.remove("finalDoorCommand");
        wire.put("completionReason", 3);

        ObjectNode expectedEvent =
                (ObjectNode) objectMapper.readTree(
                        Files.readString(contractPath(
                                "contracts/examples/onenet/"
                                        + "delivery-complete.event.json")));
        ObjectNode expectedPayload =
                (ObjectNode) expectedEvent.path("payload");
        expectedPayload.putNull("firstPreOpenMeasurement");
        expectedPayload.putNull("finalPostCloseMeasurement");
        expectedPayload.putNull("deliveryNetWeightGrams");
        expectedPayload.putNull("finalDoorCommand");
        expectedPayload.put(
                "completionReason",
                "TERMINAL_WEIGHT_FAILURE");
        wire.put(
                "payloadSha256",
                canonicalHash(expectedPayload));

        dispatcher.handle(
                decrypted(wire),
                "mq-terminal-weight-failure",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode actualPayload = objectMapper.readTree(
                        captor.getValue().normalizedPayload())
                .path("event")
                .path("payload");
        assertEquals(
                canonicalHash(expectedPayload),
                canonicalHash(actualPayload));
    }

    @Test
    void deliveryMustContainEachStandardPhotoSlotOnce()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ArrayNode photos = (ArrayNode) wire.path("photos");
        ((ObjectNode) photos.get(3)).put(
                "slot",
                "BEFORE_INNER");

        assertPermanentlyRejected(wire);
    }

    @Test
    void availablePhotoRequiresCapturedIdentityAndUrl()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ArrayNode photos = (ArrayNode) wire.path("photos");
        ((ObjectNode) photos.get(0)).put("status", 1);

        assertPermanentlyRejected(wire);
    }

    @Test
    void capturedPendingPhotoStillRequiresReason()
            throws Exception {
        ObjectNode wire = deliveryWireValue();
        ObjectNode photo =
                (ObjectNode) wire.path("photos").get(0);
        photo.put("photoUidPresent", true);
        photo.put(
                "photoUid",
                "30000000-0000-4000-8000-000000000020");
        photo.put("sha256Present", true);
        photo.put("sha256", "c".repeat(64));
        photo.put("sizeBytesPresent", true);
        photo.put("sizeBytes", 1024);
        photo.put("capturedAtPresent", true);
        photo.put("capturedAt", "2026-07-24T01:00:20.000Z");
        photo.put("missingReasonPresent", false);
        photo.put("missingReason", "");

        assertPermanentlyRejected(wire);
    }

    private void assertPermanentlyRejected(ObjectNode wire)
            throws Exception {
        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(wire),
                        "mq-invalid-delivery",
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
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        ObjectNode params = subData.putObject("params");
        params.putObject("deliveryComplete").set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode deliveryWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "delivery-complete.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("deliveryComplete")
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
