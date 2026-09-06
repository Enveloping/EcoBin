package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
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
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
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
        when(sourceScopePort.resolverForOrganizationAsset(HARDWARE_SN))
                .thenReturn(resolver);
        when(sourceScopePort.resolverForBusinessConfirmation(
                eq(HARDWARE_SN), anyString())).thenReturn(resolver);
        when(sourceScopePort.resolverForPlatformAsset(HARDWARE_SN))
                .thenReturn(TrustedInboxScopeResolver.platform());
        when(sourceScopePort.resolverForPermanentAssetFact(
                eq(HARDWARE_SN), any(Instant.class)))
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
        assertThat(message.normalizedSchemaVersion())
                .isEqualTo(
                        TrustedDeviceInboxEvent
                                .CURRENT_NORMALIZED_SCHEMA_VERSION);
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
            assertThat(message.normalizedSchemaVersion())
                    .isEqualTo(
                            TrustedDeviceInboxEvent
                                    .CURRENT_NORMALIZED_SCHEMA_VERSION);
            JsonNode normalized =
                    objectMapper.readTree(message.normalizedPayload());
            assertThat(normalized.path("event")
                    .path("eventType").asText())
                    .isEqualTo(message.messageKind());
            assertThat(normalized.path("eventCanonicalSha256")
                    .asText())
                    .matches("[0-9a-f]{64}");
            if ("DEVICE_RUNTIME_SNAPSHOT".equals(
                    message.messageKind())) {
                JsonNode identity = normalized.path("event")
                        .path("payload")
                        .path("mcuFirmwareIdentity");
                assertThat(identity.path("queryStatus").asText())
                        .isEqualTo("OK");
                assertThat(identity.path("statusCode").asInt())
                        .isZero();
                assertThat(identity.path("fixedFrameRevision").asInt())
                        .isEqualTo(2);
            }
        }
        verify(sourceScopePort,
                org.mockito.Mockito.times(4))
                .resolverForPermanentAssetFact(
                        eq(HARDWARE_SN), any(Instant.class));
    }

    @Test
    void deviceSoftwareStateIsNormalizedAsAPlatformAssetFact()
            throws Exception {
        JsonNode wireExample = objectMapper.readTree(Files.readString(
                contractPath("contracts/examples/onenet-wire/"
                        + "device-software-state-reported.event-wire.json")));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        dispatcher.handle(
                decrypted,
                "mq-device-software-state",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertThat(message.sourceNamespace())
                .isEqualTo("onenet.device-software-state");
        assertThat(message.messageKind())
                .isEqualTo("DEVICE_SOFTWARE_STATE_REPORTED");
        assertThat(message.normalizedSchemaVersion())
                .isEqualTo(
                        TrustedDeviceInboxEvent
                                .CURRENT_NORMALIZED_SCHEMA_VERSION);
        assertThat(message.executionLane())
                .isEqualTo(
                        org.enveloping.ecobin.operations.api.inbox
                                .TrustedInboxExecutionLane.DEVICE);

        JsonNode normalized =
                objectMapper.readTree(message.normalizedPayload());
        JsonNode payload = normalized.path("event").path("payload");
        assertThat(payload.path("managementArchitectureGeneration")
                .asText()).isEqualTo("PERMANENT_V1");
        assertThat(payload.path("businessAdmissionState").asText())
                .isEqualTo("OPEN");
        assertThat(payload.path("activeBusinessRelease")
                .path("releaseSequence").asLong()).isEqualTo(12L);
        assertThat(payload.path("negotiatedProtocols")
                .path("agentBusinessNegotiated").asBoolean()).isTrue();
        assertThat(payload.path("negotiatedProtocols")
                .path("agentBusinessPresent").isMissingNode()).isTrue();
        assertThat(payload.path("mcuFirmware")
                .path("versionCode").asLong()).isEqualTo(20_100L);

        message.scopeResolver().resolve(
                (scopeKind, tenantKey, organizationKey) -> {
                    assertThat(scopeKind).isEqualTo("PLATFORM");
                    assertThat(tenantKey).isNull();
                    assertThat(organizationKey).isNull();
                });
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
    }

    @Test
    void imageBridgeAndWholeDecimalSequenceAreNormalized()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-software-state-reported"
                                + ".event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceSoftwareStateReported")
                .path("value");
        wire.put("activeBusinessReleasePresent", false);
        wire.put("managementStateSequence", new BigDecimal("10.0"));
        ((ObjectNode) wire.path("communicationAgent"))
                .put("versionName", "communication-20260906-31");
        ((ObjectNode) wire.path("deviceUpdater"))
                .put("versionName", "updater-20260906-31");

        ObjectNode semantic = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-software-state-reported"
                                + ".event.json")));
        ObjectNode semanticPayload =
                (ObjectNode) semantic.path("payload");
        semanticPayload.putNull("activeBusinessRelease");
        semanticPayload.put("managementStateSequence", 10L);
        ((ObjectNode) semanticPayload.path("communicationAgent"))
                .put("versionName", "communication-20260906-31");
        ((ObjectNode) semanticPayload.path("deviceUpdater"))
                .put("versionName", "updater-20260906-31");
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(
                        objectMapper.convertValue(
                                semanticPayload, Map.class)));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        dispatcher.handle(
                decrypted,
                "mq-device-software-image-bridge",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode payload = objectMapper.readTree(
                        captor.getValue().normalizedPayload())
                .path("event").path("payload");
        assertThat(payload.path("activeBusinessRelease").isNull()).isTrue();
        assertThat(payload.path("managementStateSequence").asLong())
                .isEqualTo(10L);
        assertThat(payload.path("businessReady").asBoolean()).isTrue();
    }

    @Test
    void mixedImageGenerationsCannotClaimAReadyImageBridge()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-software-state-reported"
                                + ".event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceSoftwareStateReported")
                .path("value");
        wire.put("activeBusinessReleasePresent", false);
        ((ObjectNode) wire.path("communicationAgent"))
                .put("versionName", "communication-20260906-30");
        ((ObjectNode) wire.path("deviceUpdater"))
                .put("versionName", "updater-20260906-31");
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        OneNetPermanentMessageException exception = assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted,
                        "mq-device-software-mixed-image-generation",
                        RAW_TRANSPORT));

        assertThat(exception).hasMessageContaining(
                "ready business software lacks its active release");
        verify(inboxPort, never()).receive(any());
    }

    @Test
    void fractionalDeviceSoftwareSequenceIsRejected()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-software-state-reported"
                                + ".event-wire.json")));
        ((ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceSoftwareStateReported")
                .path("value"))
                .put("managementStateSequence", new BigDecimal("10.5"));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        OneNetPermanentMessageException exception = assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted,
                        "mq-device-software-fractional-sequence",
                        RAW_TRANSPORT));

        assertThat(exception).hasMessage(
                "managementStateSequence must be an integer");
        verify(inboxPort, never()).receive(any());
    }

    @Test
    void deviceSoftwareDeclaredProtocolMajorMustBePositive()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-software-state-reported"
                                + ".event-wire.json")));
        ((ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceSoftwareStateReported")
                .path("value")
                .path("communicationAgent"))
                .put("managementTransportProtocolMajor", 0);
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        OneNetPermanentMessageException exception = assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted,
                        "mq-device-software-zero-major",
                        RAW_TRANSPORT));

        assertThat(exception).hasMessage(
                "managementTransportProtocolMajor"
                        + " is outside the target range");
        verify(inboxPort, never()).receive(any());
    }

    @Test
    void runtimeSnapshotBeforeIdentityFieldDeploymentRemainsValid()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-runtime-snapshot.event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceRuntimeSnapshot")
                .path("value");
        wire.remove("mcuFirmwareIdentityPresent");
        wire.remove("mcuFirmwareIdentity");

        ObjectNode semantic = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-runtime-snapshot.event.json")));
        ObjectNode payload = (ObjectNode) semantic.path("payload");
        payload.remove("mcuFirmwareIdentity");
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(
                        objectMapper.convertValue(payload, Map.class)));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        dispatcher.handle(
                decrypted,
                "mq-runtime-before-f3-identity",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode normalized = objectMapper.readTree(
                captor.getValue().normalizedPayload());
        assertThat(normalized.path("event").path("payload")
                .has("mcuFirmwareIdentity")).isFalse();
    }

    @Test
    void fixedFrameRuntimeWithVerifiedFirmwareIdentityIsAccepted()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-runtime-snapshot.event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceRuntimeSnapshot")
                .path("value");
        wire.put("mcuFirmwareVersion", "1.2.3");
        ObjectNode wireIdentity =
                (ObjectNode) wire.path("mcuFirmwareIdentity");
        wireIdentity.put("firmwareVersion", "1.2.3");
        wireIdentity.put("firmwareVersionCode", 10_203);
        wireIdentity.put(
                "firmwareIdentityHex", "0102030405060708");
        wire.put("uartProtocolMajorPresent", false);
        wire.put("uartProtocolMinorPresent", false);
        wire.path("ports").forEach(portNode -> {
            ObjectNode port = (ObjectNode) portNode;
            port.put("weightValueKind", 5);
            port.put("weightSampleCount", 1);
        });

        ObjectNode semantic = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-runtime-snapshot.event.json")));
        ObjectNode payload = (ObjectNode) semantic.path("payload");
        payload.put("mcuFirmwareVersion", "1.2.3");
        ObjectNode identity =
                (ObjectNode) payload.path("mcuFirmwareIdentity");
        identity.put("firmwareVersion", "1.2.3");
        identity.put("firmwareVersionCode", 10_203);
        identity.put("firmwareIdentityHex", "0102030405060708");
        payload.putNull("uartProtocolMajor");
        payload.putNull("uartProtocolMinor");
        payload.path("ports").forEach(portNode -> {
            ObjectNode port = (ObjectNode) portNode;
            port.put("weightValueKind", "LAST_OBSERVED");
            port.put("weightSampleCount", 1);
        });
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(
                        objectMapper.convertValue(payload, Map.class)));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        dispatcher.handle(
                decrypted,
                "mq-fixed-frame-runtime-with-f3-identity",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode normalized = objectMapper.readTree(
                captor.getValue().normalizedPayload());
        JsonNode normalizedPayload =
                normalized.path("event").path("payload");
        assertThat(normalizedPayload.path("mcuFirmwareVersion").asText())
                .isEqualTo("1.2.3");
        assertThat(normalizedPayload.path("mcuFirmwareIdentity")
                .path("fixedFrameRevision").asInt()).isEqualTo(2);
        assertThat(normalizedPayload.path("uartProtocolMajor").isNull())
                .isTrue();
        assertThat(normalizedPayload.path("uartProtocolMinor").isNull())
                .isTrue();
        assertThat(normalizedPayload.path("ports").get(0)
                .path("weightValueKind").asText())
                .isEqualTo("LAST_OBSERVED");
    }

    @Test
    void legacyFixedFrameRuntimeWithoutFirmwareIdentityRemainsAccepted()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-runtime-snapshot.event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceRuntimeSnapshot")
                .path("value");
        wire.put("mcuFirmwareVersion", "fixed-frame-compat");
        wire.remove("mcuFirmwareIdentityPresent");
        wire.remove("mcuFirmwareIdentity");
        wire.put("uartProtocolMajorPresent", false);
        wire.put("uartProtocolMinorPresent", false);
        wire.path("ports").forEach(portNode -> {
            ObjectNode port = (ObjectNode) portNode;
            port.put("weightValueKind", 5);
            port.put("weightSampleCount", 0);
        });

        ObjectNode semantic = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-runtime-snapshot.event.json")));
        ObjectNode payload = (ObjectNode) semantic.path("payload");
        payload.put("mcuFirmwareVersion", "fixed-frame-compat");
        payload.remove("mcuFirmwareIdentity");
        payload.putNull("uartProtocolMajor");
        payload.putNull("uartProtocolMinor");
        payload.path("ports").forEach(portNode -> {
            ObjectNode port = (ObjectNode) portNode;
            port.put("weightValueKind", "LAST_OBSERVED");
            port.put("weightSampleCount", 0);
        });
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(
                        objectMapper.convertValue(payload, Map.class)));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        dispatcher.handle(
                decrypted,
                "mq-legacy-fixed-frame-runtime",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        JsonNode normalized = objectMapper.readTree(
                captor.getValue().normalizedPayload());
        JsonNode normalizedPayload =
                normalized.path("event").path("payload");
        assertThat(normalizedPayload.path("mcuFirmwareVersion").asText())
                .isEqualTo("fixed-frame-compat");
        assertThat(normalizedPayload.has("mcuFirmwareIdentity")).isFalse();
        assertThat(normalizedPayload.path("uartProtocolMajor").isNull())
                .isTrue();
        assertThat(normalizedPayload.path("ports").get(0)
                .path("weightValueKind").asText())
                .isEqualTo("LAST_OBSERVED");
    }

    @Test
    void runtimeSnapshotRequiresCompleteUartProtocolVersionPair()
            throws Exception {
        ObjectNode wireExample = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet-wire/"
                                + "device-runtime-snapshot.event-wire.json")));
        ObjectNode wire = (ObjectNode) wireExample.path("oneJsonPayload")
                .path("params")
                .path("deviceRuntimeSnapshot")
                .path("value");
        wire.put("uartProtocolMajorPresent", false);

        ObjectNode semantic = (ObjectNode) objectMapper.readTree(
                Files.readString(contractPath(
                        "contracts/examples/onenet/"
                                + "device-runtime-snapshot.event.json")));
        ObjectNode payload = (ObjectNode) semantic.path("payload");
        payload.putNull("uartProtocolMajor");
        wire.put(
                "payloadSha256",
                OneNetCanonicalJson.payloadSha256(
                        objectMapper.convertValue(payload, Map.class)));
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
                wireExample.path("oneJsonPayload")
                        .path("params").toString());

        OneNetPermanentMessageException exception = assertThrows(
                OneNetPermanentMessageException.class,
                () -> dispatcher.handle(
                        decrypted,
                        "mq-runtime-incomplete-uart-version",
                        RAW_TRANSPORT));

        assertThat(exception).hasMessage(
                "UART protocol version fields differ");
        verify(inboxPort, never()).receive(any());
    }

    @Test
    void deviceLifecycleNotificationBecomesPlatformScopedReliableFact()
            throws Exception {
        dispatcher.handle(
                """
                {
                  "msgType": "deviceOffline",
                  "subData": {
                    "productId": "%s",
                    "deviceName": "%s",
                    "time": 1785603630000
                  }
                }
                """.formatted(PRODUCT_ID, HARDWARE_SN),
                "persistent://tenant/ns/topic-ledger-entry-42",
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        TrustedInboxMessage message = captor.getValue();
        assertThat(message.sourceNamespace())
                .isEqualTo("onenet.device-lifecycle");
        assertThat(message.externalMessageId())
                .isEqualTo("persistent://tenant/ns/topic-ledger-entry-42");
        assertThat(message.messageKind())
                .isEqualTo("DEVICE_TRANSPORT_STATUS_CHANGED");
        assertThat(message.executionLane())
                .isEqualTo(
                        org.enveloping.ecobin.operations.api.inbox
                                .TrustedInboxExecutionLane.DEVICE);
        JsonNode normalized =
                objectMapper.readTree(message.normalizedPayload());
        assertThat(normalized.path("presence").path("status").asText())
                .isEqualTo("OFFLINE");
        assertThat(normalized.path("presence").path("observedAt").asText())
                .isEqualTo("2026-08-01T17:00:30Z");

        message.scopeResolver().resolve(
                (scopeKind, tenantKey, organizationKey) -> {
                    assertThat(scopeKind).isEqualTo("PLATFORM");
                    assertThat(tenantKey).isNull();
                    assertThat(organizationKey).isNull();
                });
        verify(sourceScopePort).resolverForPlatformAsset(HARDWARE_SN);
    }

    @Test
    void longLifecycleMqMessageIdUsesCollisionResistantDigest()
            throws Exception {
        String mqMessageId = "persistent://tenant/ns/topic/"
                + "ledger-entry-".repeat(20);

        dispatcher.handle(
                """
                {
                  "msgType": "deviceOnline",
                  "subData": {
                    "productId": "%s",
                    "deviceName": "%s",
                    "time": 1785603630000
                  }
                }
                """.formatted(PRODUCT_ID, HARDWARE_SN),
                mqMessageId,
                RAW_TRANSPORT);

        ArgumentCaptor<TrustedInboxMessage> captor =
                ArgumentCaptor.forClass(TrustedInboxMessage.class);
        verify(inboxPort).receive(captor.capture());
        String expectedDigest = HexFormat.of().formatHex(
                MessageDigest.getInstance("SHA-256").digest(
                        mqMessageId.getBytes(StandardCharsets.UTF_8)));
        assertThat(captor.getValue().externalMessageId())
                .isEqualTo("sha256:" + expectedDigest);
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
                .resolverForOrganizationAsset(any());
        verify(sourceScopePort, never())
                .resolverForPlatformAsset(any());
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
