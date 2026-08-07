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
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherAcceptanceTest {

    private static final String PRODUCT_ID = "ecobin-product-contract";
    private static final String HARDWARE_SN = "SN-CONTRACT-0001";
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
                decrypted(acceptanceWireValue()),
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

    private String decrypted(ObjectNode value) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("msgType", "thingEvent");
        ObjectNode subData = root.putObject("subData");
        subData.put("productId", PRODUCT_ID);
        subData.put("deviceName", HARDWARE_SN);
        subData.putObject("params")
                .putObject("deviceAcceptanceEvidence")
                .set("value", value);
        return objectMapper.writeValueAsString(root);
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
