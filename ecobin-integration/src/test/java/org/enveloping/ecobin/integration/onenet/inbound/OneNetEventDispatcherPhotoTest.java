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
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherPhotoTest {

    private static final String PRODUCT_ID =
            "ecobin-product-contract";
    private static final String HARDWARE_SN =
            "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-photo-transport-evidence"
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
        when(sourceScopePort.resolverFor(
                HARDWARE_SN, "Dp_demo_01"))
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
    void hardwarePhotoStatusWireNormalizesToSemanticContract()
            throws Exception {
        ObjectNode wire = photoWireValue();

        dispatcher.handle(
                decrypted("photoStatusReported", wire),
                "mq-photo-status",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "PHOTO_STATUS_REPORTED",
                message.messageKind());

        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "photo-status-reported.event.json")));
        assertEquals(
                canonicalHash(expected),
                canonicalHash(normalized));
        assertEquals(
                expected.path("payloadSha256").asText(),
                normalized.path("payloadSha256").asText());
    }

    @Test
    void hardwarePhotoGrantRequestNormalizesToSemanticContract()
            throws Exception {
        ObjectNode wire = grantRequestWireValue();

        dispatcher.handle(
                decrypted("photoUploadGrantRequested", wire),
                "mq-photo-grant-request",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "PHOTO_UPLOAD_GRANT_REQUESTED",
                message.messageKind());
        JsonNode normalized = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "photo-upload-grant-requested"
                                + ".event.json")));
        assertEquals(
                canonicalHash(expected),
                canonicalHash(normalized));
    }

    @Test
    void photoGrantSlotsMustUseStableWorkOrder()
            throws Exception {
        ObjectNode wire = grantRequestWireValue();
        wire.withArray("requestedSlots").removeAll()
                .add(4)
                .add(3);

        assertPermanentlyRejected(
                "photoUploadGrantRequested",
                wire);
    }

    @Test
    void photoTargetMustIdentifyPayloadWork() throws Exception {
        ObjectNode wire = photoWireValue();
        ((ObjectNode) wire.path("target")).put(
                "uid",
                "30000000-0000-4000-8000-000000000099");

        assertPermanentlyRejected(
                "photoStatusReported", wire);
    }

    @Test
    void cleanWorkCannotUseDeliveryPhotoSlot() throws Exception {
        ObjectNode wire = photoWireValue();
        wire.put("workType", 2);
        ((ObjectNode) wire.path("target")).put("type", 2);

        assertPermanentlyRejected(
                "photoStatusReported", wire);
    }

    private void assertPermanentlyRejected(
            String identifier,
            ObjectNode wire)
            throws Exception {
        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(identifier, wire),
                        "mq-invalid-photo",
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

    private String decrypted(
            String identifier,
            ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        ObjectNode params = subData.putObject("params");
        params.putObject(identifier)
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode photoWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "photo-status-reported.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("photoStatusReported")
                .path("value")
                .deepCopy();
    }

    private ObjectNode grantRequestWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "photo-upload-grant-requested"
                                + ".event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("photoUploadGrantRequested")
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
