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

class OneNetEventDispatcherFullnessStateTest {

    private static final String PRODUCT_ID =
            "ecobin-product-contract";
    private static final String HARDWARE_SN =
            "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-fullness-state-evidence"
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
        CosProperties cos = new CosProperties();
        cos.setBaseUrl(
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
                cos,
                objectMapper);
    }

    @Test
    void fullnessStateWireNormalizesToFrozenSemanticExample()
            throws Exception {
        dispatcher.handle(
                decrypted(fullnessWireValue()),
                "mq-fullness-state",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertEquals("FULLNESS_STATE_CHANGED", message.messageKind());
        assertEquals(
                "51000000-0000-4000-8000-000000000002",
                message.externalMessageId());

        JsonNode actual = objectMapper.readTree(
                        message.normalizedPayload())
                .path("event");
        JsonNode expected = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet/"
                                + "fullness-state-changed.event.json")));
        assertEquals(canonicalHash(expected), canonicalHash(actual));
        assertEquals(
                "PORT_FULLNESS_STATE",
                actual.path("target").path("type").asText());
        assertEquals(
                "FULL",
                actual.path("payload").path("state").asText());
    }

    @Test
    void fullnessStateTargetMustIdentifyStateChange()
            throws Exception {
        ObjectNode wire = fullnessWireValue();
        ((ObjectNode) wire.path("target")).put(
                "uid",
                "51000000-0000-4000-8000-000000000099");

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(wire),
                        "mq-invalid-fullness-state",
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
                .putObject("fullnessStateChanged")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
    }

    private ObjectNode fullnessWireValue() throws Exception {
        JsonNode example = objectMapper.readTree(Files.readString(
                contractPath(
                        "contracts/examples/onenet-wire/"
                                + "fullness-state-changed.event-wire.json")));
        return (ObjectNode) example.path("oneJsonPayload")
                .path("params")
                .path("fullnessStateChanged")
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
