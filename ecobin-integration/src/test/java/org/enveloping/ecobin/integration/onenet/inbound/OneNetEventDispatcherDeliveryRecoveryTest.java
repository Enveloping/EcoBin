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

class OneNetEventDispatcherDeliveryRecoveryTest {

    private static final String PRODUCT_ID = "ecobin-product-contract";
    private static final String HARDWARE_SN = "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-recovery-evidence".getBytes(StandardCharsets.UTF_8);

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
        TrustedInboxScopeResolver resolver = writer -> writer.platform();
        when(sourceScopePort.resolverForPlatformAsset(HARDWARE_SN))
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
    void recoveryWireNormalizesToDistinctIssueOnlyEvent() throws Exception {
        dispatcher.handle(
                decrypted(recoveryWireValue()),
                "mq-delivery-recovery-quarantined",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals(
                "DELIVERY_RECOVERY_QUARANTINED",
                message.messageKind());
        assertEquals(
                "31000000-0000-4000-8000-000000000001",
                message.externalMessageId());

        JsonNode actualEvent = objectMapper
                .readTree(message.normalizedPayload())
                .path("event");
        JsonNode expectedEvent = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet/"
                                + "delivery-recovery-quarantined.event.json")));
        assertEquals(canonicalHash(expectedEvent), canonicalHash(actualEvent));
        assertEquals(
                "NONE",
                actualEvent.path("payload").path("businessValue").asText());
        assertEquals(
                "UNKNOWN_EFFECT_QUARANTINED",
                actualEvent.path("payload")
                        .path("deviceEvidence")
                        .path("resolutionState").asText());
    }

    @Test
    void recoveryRejectsUnchangedBootIdentity() throws Exception {
        ObjectNode wire = recoveryWireValue();
        ObjectNode evidence = (ObjectNode) wire.path("deviceEvidence");
        evidence.put(
                "currentBootIdentity",
                evidence.path("previousBootIdentity").asText());

        assertPermanentlyRejected(wire);
    }

    @Test
    void recoveryRejectsAnyFalseOperatorConfirmation() throws Exception {
        ObjectNode wire = recoveryWireValue();
        ((ObjectNode) wire.path("operatorConfirmations"))
                .put("motionAreaClearConfirmed", false);

        assertPermanentlyRejected(wire);
    }

    @Test
    void recoveryRejectsLedgerIdentityFromAnotherDelivery() throws Exception {
        ObjectNode wire = recoveryWireValue();
        ((ObjectNode) wire.path("deviceEvidence")).put(
                "workUid",
                "31000000-0000-4000-8000-000000000099");

        assertPermanentlyRejected(wire);
    }

    private void assertPermanentlyRejected(ObjectNode wire) throws Exception {
        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(wire),
                        "mq-invalid-delivery-recovery",
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
        subData.putObject("params")
                .putObject("deliveryRecoveryQuarantined")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode recoveryWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "delivery-recovery-quarantined.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("deliveryRecoveryQuarantined")
                .path("value")
                .deepCopy();
    }

    private static Path contractPath(String relative) {
        Path workingDirectory = Path.of("").toAbsolutePath().normalize();
        Path repository = Files.isDirectory(
                workingDirectory.resolve("contracts"))
                ? workingDirectory
                : workingDirectory.getParent();
        return repository.resolve(relative).normalize();
    }
}
