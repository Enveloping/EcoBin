package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetPermanentMessageException;
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

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetEventDispatcherTest {

    private static final String PRODUCT_ID =
            "ecobin-product-contract";
    private static final String HARDWARE_SN =
            "SN-CONTRACT-0001";
    private static final byte[] RAW_TRANSPORT =
            "encrypted-transport-evidence"
                    .getBytes(StandardCharsets.UTF_8);

    private TrustedInboxPort inboxPort;
    private TrustedDeviceSourceScopePort sourceScopePort;
    private ObjectMapper objectMapper;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        inboxPort = mock(TrustedInboxPort.class);
        sourceScopePort =
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
    void targetConfigurationProgressIsNormalizedIntoReliableInbox() {
        dispatcher.handle(
                decrypted(configurationWireValue()),
                "mq-message-1",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertThat(message.sourceNamespace())
                .isEqualTo("onenet.device-event");
        assertThat(message.externalMessageId())
                .isEqualTo(
                        "85000000-0000-4000-8000-000000000001");
        assertThat(message.messageKind())
                .isEqualTo("CONFIGURATION_PROGRESS");
        assertThat(message.rawTransportBody())
                .isEqualTo(RAW_TRANSPORT);
        assertThat(message.authenticationPrincipalRef())
                .doesNotContain("accessKey")
                .doesNotContain("secret");

        JsonNode normalized =
                objectMapper.readTree(message.normalizedPayload());
        assertThat(normalized.path("trustedSource")
                .path("productId").asText())
                .isEqualTo(PRODUCT_ID);
        assertThat(normalized.path("trustedSource")
                .path("deviceName").asText())
                .isEqualTo(HARDWARE_SN);
        assertThat(normalized.path("event")
                .path("payload").path("stage").asText())
                .isEqualTo("APPLIED");
        assertThat(normalized.path("eventCanonicalSha256")
                .asText())
                .matches("[0-9a-f]{64}");

        AtomicLong tenant = new AtomicLong();
        AtomicLong organization = new AtomicLong();
        message.scopeResolver().resolve(
                (scopeKind, tenantKey, organizationKey) -> {
                    assertThat(scopeKind).isEqualTo("ORGANIZATION");
                    tenant.set(tenantKey);
                    organization.set(organizationKey);
                });
        assertThat(tenant).hasValue(11);
        assertThat(organization).hasValue(22);
    }

    @Test
    void everyTrustedOrangePiRuntimeFactIsNormalizedIntoReliableInbox()
            throws Exception {
        Map<String, String> contracts = Map.of(
                "device-runtime-snapshot.event-wire.json",
                "DEVICE_RUNTIME_SNAPSHOT",
                "device-fault-observed.event-wire.json",
                "DEVICE_FAULT_OBSERVED",
                "device-fault-recovered.event-wire.json",
                "DEVICE_FAULT_RECOVERED",
                "safety-sensor-state-changed.event-wire.json",
                "SAFETY_SENSOR_STATE_CHANGED",
                "business-confirmation-receipt.event-wire.json",
                "BUSINESS_CONFIRMATION_RECEIPT");

        for (Map.Entry<String, String> contract : contracts.entrySet()) {
            JsonNode example = objectMapper.readTree(Files.readString(
                    contractPath("contracts/examples/onenet-wire/"
                            + contract.getKey())));
            String decrypted = """
                    {
                      "msgType": "thingEvent",
                      "subData": {
                        "productId": "%s",
                        "deviceName": "%s",
                        "params": %s
                      }
                    }
                    """.formatted(
                    PRODUCT_ID,
                    HARDWARE_SN,
                    example.path("oneJsonPayload")
                            .path("params").toString());

            dispatcher.handle(
                    decrypted,
                    "mq-" + contract.getKey(),
                    RAW_TRANSPORT);
        }

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort,
                org.mockito.Mockito.times(contracts.size()))
                .receive(captor.capture());
        assertThat(captor.getAllValues())
                .extracting(TrustedInboxMessage::messageKind)
                .containsExactlyInAnyOrderElementsOf(contracts.values());
        for (TrustedInboxMessage message : captor.getAllValues()) {
            JsonNode normalized =
                    objectMapper.readTree(message.normalizedPayload());
            assertThat(normalized.path("event")
                    .path("eventType").asText())
                    .isEqualTo(message.messageKind());
            assertThat(normalized.path("eventCanonicalSha256")
                    .asText())
                    .matches("[0-9a-f]{64}");
        }
    }

    @Test
    void payloadDigestMismatchIsPermanentlyRejected() {
        String invalid = configurationWireValue().replace(
                "d82854d30f82edbd441a9d94e96c9f9649c7d7ba198db1ec54190084c483de37",
                "a82854d30f82edbd441a9d94e96c9f9649c7d7ba198db1ec54190084c483de37");

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted(invalid),
                        "mq-message-2",
                        RAW_TRANSPORT));
        verify(inboxPort, never()).receive(any());
    }

    @Test
    void legacyThingModelIdentifierDoesNotReachBusinessOrInbox() {
        String legacy = """
                {
                  "msgType": "thingEvent",
                  "subData": {
                    "productId": "%s",
                    "deviceName": "%s",
                    "params": {
                      "cleanGross": {
                        "value": {"cleanOrderId": 1, "weight": 12.5}
                      }
                    }
                  }
                }
                """.formatted(PRODUCT_ID, HARDWARE_SN);

        dispatcher.handle(
                legacy, "mq-legacy", RAW_TRANSPORT);

        verify(inboxPort, never()).receive(any());
        verify(sourceScopePort, never())
                .resolverFor(any(), any());
    }

    @Test
    void productIdentityMustMatchConfiguredEpoch() {
        String wrongProduct = decrypted(
                configurationWireValue())
                .replace(PRODUCT_ID, "other-product");

        assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        wrongProduct,
                        "mq-message-3",
                        RAW_TRANSPORT));
        verify(inboxPort, never()).receive(any());
    }

    private static String decrypted(String value) {
        return """
                {
                  "msgType": "thingEvent",
                  "subData": {
                    "productId": "%s",
                    "deviceName": "%s",
                    "params": {
                      "configurationProgress": {
                        "value": %s
                      }
                    }
                  }
                }
                """.formatted(PRODUCT_ID, HARDWARE_SN, value);
    }

    private static String configurationWireValue() {
        return """
                {
                  "applicationUid": "10000000-0000-4000-8000-000000000001",
                  "clockQuality": 1,
                  "commandUid": "20000000-0000-4000-8000-000000000001",
                  "contentSha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "deliveryClass": 1,
                  "deploymentCode": "Dp_demo_01",
                  "edgeEventSequence": 1047,
                  "errorCode": "",
                  "errorCodePresent": false,
                  "eventType": 1,
                  "eventUid": "85000000-0000-4000-8000-000000000001",
                  "mcuCommandUid": "85000000-0000-4000-8000-000000000002",
                  "mcuCommandUidPresent": true,
                  "mcuPayloadSha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                  "occurredAt": "2026-07-24T01:00:30.000Z",
                  "occurredAtPresent": true,
                  "payloadSha256": "d82854d30f82edbd441a9d94e96c9f9649c7d7ba198db1ec54190084c483de37",
                  "schemaVersion": 1,
                  "stage": 2,
                  "target": {
                    "type": 1,
                    "uid": "10000000-0000-4000-8000-000000000001"
                  },
                  "version": 8
                }
                """;
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
