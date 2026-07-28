package org.enveloping.ecobin.integration.onenet.inbound;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * OneNet target-contract inbound Adapter.
 *
 * <p>Only {@code configurationProgress} is currently registered. Legacy
 * identifiers are deliberately rejected without invoking old business
 * interfaces. A valid event is normalized, digest-checked and durably
 * accepted by the operations inbox before the Pulsar transport is ACKed.</p>
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class OneNetEventDispatcher implements OneNetMessageHandler {

    private static final String CONFIGURATION_PROGRESS =
            "configurationProgress";
    private static final long SAFE_INTEGER_MAX =
            9_007_199_254_740_991L;
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";

    private final TrustedInboxPort trustedInboxPort;
    private final TrustedDeviceSourceScopePort sourceScopePort;
    private final OneNetProperties oneNetProperties;
    private final ObjectMapper objectMapper;

    @Override
    public void handle(
            String decryptedJson,
            String mqMessageId,
            byte[] rawTransportBody) {
        JsonNode root;
        try {
            root = objectMapper.readTree(decryptedJson);
        } catch (RuntimeException exception) {
            throw permanent("decrypted OneNet body is not JSON", exception);
        }
        String msgType = text(root, "msgType", 32);
        if (!"thingEvent".equals(msgType)) {
            log.info(
                    "[OneNet·分发] target Adapter skipped msgType={}",
                    safeToken(msgType));
            return;
        }
        JsonNode subData = object(root, "subData");
        String hardwareSn = text(subData, "deviceName", 64);
        String productId = text(subData, "productId", 128);
        if (oneNetProperties.getProductId() == null
                || !oneNetProperties.getProductId().equals(productId)) {
            throw permanent(
                    "authenticated OneNet product does not match runtime epoch");
        }
        JsonNode params = object(subData, "params");
        for (String identifier : params.propertyNames()) {
            if (CONFIGURATION_PROGRESS.equals(identifier)) {
                acceptConfigurationProgress(
                        productId,
                        hardwareSn,
                        unwrap(params.get(identifier)),
                        rawTransportBody);
            } else {
                log.warn(
                        "[OneNet·分发] target Adapter rejected identifier={} device={}",
                        safeToken(identifier),
                        safeToken(hardwareSn));
            }
        }
    }

    private void acceptConfigurationProgress(
            String productId,
            String hardwareSn,
            JsonNode wire,
            byte[] rawTransportBody) {
        try {
            Map<String, Object> payload = configurationPayload(wire);
            String payloadSha256 = pattern(
                    wire, "payloadSha256", SHA256);
            String computedPayloadSha256 =
                    OneNetCanonicalJson.payloadSha256(payload);
            if (!payloadSha256.equals(computedPayloadSha256)) {
                throw permanent(
                        "configuration progress payload digest differs");
            }

            Map<String, Object> event = new LinkedHashMap<>();
            event.put(
                    "schemaVersion",
                    exactEnum(wire, "schemaVersion", 1, 1L));
            event.put("eventUid", pattern(wire, "eventUid", UUID_V4));
            event.put(
                    "deploymentCode",
                    pattern(
                            wire,
                            "deploymentCode",
                            "^Dp_[A-Za-z0-9_-]{6,61}$"));
            event.put(
                    "edgeEventSequence",
                    positiveSafeInteger(wire, "edgeEventSequence"));
            event.put(
                    "eventType",
                    exactEnum(
                            wire,
                            "eventType",
                            1,
                            "CONFIGURATION_PROGRESS"));
            event.put(
                    "deliveryClass",
                    exactEnum(
                            wire,
                            "deliveryClass",
                            1,
                            "RELIABLE_FACT"));
            JsonNode wireTarget = object(wire, "target");
            Map<String, Object> target = new LinkedHashMap<>();
            target.put(
                    "type",
                    exactEnum(
                            wireTarget,
                            "type",
                            1,
                            "CONFIGURATION_APPLICATION"));
            target.put("uid", pattern(wireTarget, "uid", UUID_V4));
            event.put("target", target);
            event.put(
                    "commandUid",
                    pattern(wire, "commandUid", UUID_V4));
            event.put("occurredAt", occurredAt(wire));
            event.put(
                    "clockQuality",
                    enumText(
                            integer(wire, "clockQuality"),
                            Map.of(
                                    1L, "SYNCED",
                                    2L, "ESTIMATED",
                                    3L, "UNAVAILABLE"),
                            "clockQuality"));
            validateClockShape(event);
            event.put("payloadSha256", payloadSha256);
            event.put("payload", payload);

            String applicationUid =
                    (String) payload.get("applicationUid");
            if (!applicationUid.equals(target.get("uid"))) {
                throw permanent(
                        "configuration application target differs from payload");
            }
            String canonicalSha256 =
                    OneNetCanonicalJson.eventCanonicalSha256(
                            event, productId, hardwareSn);
            Map<String, Object> source = new LinkedHashMap<>();
            source.put("productId", productId);
            source.put("deviceName", hardwareSn);
            Map<String, Object> normalized = new LinkedHashMap<>();
            normalized.put("trustedSource", source);
            normalized.put(
                    "eventCanonicalSha256", canonicalSha256);
            normalized.put("event", event);

            UUID eventUid = UUID.fromString(
                    (String) event.get("eventUid"));
            UUID commandUid = UUID.fromString(
                    (String) event.get("commandUid"));
            TrustedInboxReceipt receipt = trustedInboxPort.receive(
                    new TrustedInboxMessage(
                            "onenet.device-event",
                            OneNetCanonicalJson.stablePrincipalKey(
                                    productId, hardwareSn),
                            eventUid.toString(),
                            "CONFIGURATION_PROGRESS",
                            1,
                            requireRawTransportBody(rawTransportBody),
                            objectMapper.writeValueAsString(normalized),
                            "ONENET_PULSAR_AES",
                            "product:" + productId
                                    + ";device:" + hardwareSn,
                            eventUid,
                            commandUid,
                            TrustedInboxExecutionLane.DEVICE,
                            sourceScopePort.resolverFor(
                                    hardwareSn,
                                    (String) event.get(
                                            "deploymentCode"))));
            if (!receipt.transportAcknowledgementAllowed()) {
                throw new IllegalStateException(
                        "reliable inbox did not permit transport ACK");
            }
            log.info(
                    "[OneNet·分发] configuration progress durably received event={} state={}",
                    eventUid,
                    receipt.state());
        } catch (OneNetPermanentMessageException exception) {
            throw exception;
        } catch (UntrustedInboxSourceException exception) {
            throw permanent(
                    "OneNet device source or target is not authoritative",
                    exception);
        } catch (IllegalArgumentException exception) {
            throw permanent(
                    "configuration progress violates target schema",
                    exception);
        }
    }

    private static Map<String, Object> configurationPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "applicationUid",
                pattern(wire, "applicationUid", UUID_V4));
        String stage = enumText(
                integer(wire, "stage"),
                Map.of(
                        1L, "EDGE_SAVED",
                        2L, "APPLIED",
                        3L, "FAILED"),
                "stage");
        payload.put("stage", stage);
        payload.put("version", positiveSafeInteger(wire, "version"));
        payload.put(
                "contentSha256",
                pattern(wire, "contentSha256", SHA256));
        payload.put(
                "mcuPayloadSha256",
                pattern(wire, "mcuPayloadSha256", SHA256));
        String mcuCommandUid = nullablePresenceText(
                wire,
                "mcuCommandUidPresent",
                "mcuCommandUid",
                UUID_V4);
        String errorCode = nullablePresenceText(
                wire,
                "errorCodePresent",
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$");
        boolean valid = switch (stage) {
            case "EDGE_SAVED" ->
                    mcuCommandUid == null && errorCode == null;
            case "APPLIED" ->
                    mcuCommandUid != null && errorCode == null;
            case "FAILED" -> errorCode != null;
            default -> false;
        };
        if (!valid) {
            throw permanent(
                    "configuration progress stage presence flags differ");
        }
        payload.put("mcuCommandUid", mcuCommandUid);
        payload.put("errorCode", errorCode);
        return payload;
    }

    private static String occurredAt(JsonNode wire) {
        boolean present = bool(wire, "occurredAtPresent");
        String value = textAllowEmpty(wire, "occurredAt", 30);
        if (!present) {
            if (!value.isEmpty()) {
                throw permanent(
                        "occurredAt value exists while presence flag is false");
            }
            return null;
        }
        if (!value.matches(
                "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        + "(?:\\.[0-9]{1,9})?Z$")) {
            throw permanent("occurredAt is not a target UTC instant");
        }
        try {
            Instant.parse(value);
        } catch (RuntimeException exception) {
            throw permanent("occurredAt is not a real instant", exception);
        }
        return value;
    }

    private static void validateClockShape(
            Map<String, Object> event) {
        boolean synced = "SYNCED".equals(event.get("clockQuality"));
        if (synced != (event.get("occurredAt") != null)) {
            throw permanent(
                    "clockQuality and occurredAt presence differ");
        }
    }

    private static String nullablePresenceText(
            JsonNode node,
            String presenceField,
            String valueField,
            String pattern) {
        boolean present = bool(node, presenceField);
        String value = textAllowEmpty(node, valueField, 64);
        if (!present) {
            if (!value.isEmpty()) {
                throw permanent(
                        valueField
                                + " exists while presence flag is false");
            }
            return null;
        }
        if (!value.matches(pattern)) {
            throw permanent(valueField + " has an invalid format");
        }
        return value;
    }

    private static Object exactEnum(
            JsonNode node,
            String field,
            long expectedWire,
            Object semanticValue) {
        if (integer(node, field) != expectedWire) {
            throw permanent(field + " has an unsupported enum value");
        }
        return semanticValue;
    }

    private static String enumText(
            long value,
            Map<Long, String> mapping,
            String field) {
        String result = mapping.get(value);
        if (result == null) {
            throw permanent(field + " has an unsupported enum value");
        }
        return result;
    }

    private static long positiveSafeInteger(
            JsonNode node, String field) {
        long value = integer(node, field);
        if (value <= 0 || value > SAFE_INTEGER_MAX) {
            throw permanent(field + " is outside the safe range");
        }
        return value;
    }

    private static long integer(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw permanent(field + " must be an integer");
        }
        return value.longValue();
    }

    private static boolean bool(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || !value.isBoolean()) {
            throw permanent(field + " must be a boolean");
        }
        return value.booleanValue();
    }

    private static String pattern(
            JsonNode node, String field, String pattern) {
        String value = text(node, field, 160);
        if (!value.matches(pattern)) {
            throw permanent(field + " has an invalid stable format");
        }
        return value;
    }

    private static JsonNode object(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isObject()) {
            throw permanent(field + " must be an object");
        }
        return value;
    }

    private static String text(
            JsonNode node, String field, int maximumLength) {
        String value = textAllowEmpty(node, field, maximumLength);
        if (value.isBlank()) {
            throw permanent(field + " must not be blank");
        }
        return value;
    }

    private static String textAllowEmpty(
            JsonNode node, String field, int maximumLength) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isTextual()
                || value.asText().length() > maximumLength) {
            throw permanent(field + " must be bounded text");
        }
        return value.asText();
    }

    private static JsonNode unwrap(JsonNode node) {
        if (node == null || !node.isObject()) {
            throw permanent("thing event value must be an object");
        }
        if (node.has("value")) {
            return object(node, "value");
        }
        return node;
    }

    private static byte[] requireRawTransportBody(byte[] value) {
        if (value == null || value.length == 0) {
            throw new IllegalArgumentException(
                    "raw transport body is required");
        }
        return value.clone();
    }

    private static String safeToken(String value) {
        if (value == null) {
            return "missing";
        }
        return value.replaceAll("[^A-Za-z0-9_.:-]", "_");
    }

    private static OneNetPermanentMessageException permanent(
            String message) {
        return new OneNetPermanentMessageException(message);
    }

    private static OneNetPermanentMessageException permanent(
            String message, Throwable cause) {
        return new OneNetPermanentMessageException(message, cause);
    }
}
