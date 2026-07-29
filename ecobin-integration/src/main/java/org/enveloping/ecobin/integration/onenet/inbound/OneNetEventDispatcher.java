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
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxRejection;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * OneNet target-contract inbound Adapter.
 *
 * <p>The Adapter authenticates the OneNet product/device identity, converts
 * the generated numeric wire contract into the stable semantic event contract,
 * verifies both payload and event digests, and then writes the event to the
 * reliable inbox. A successful return therefore means that Pulsar may ACK
 * without losing the business fact.</p>
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class OneNetEventDispatcher implements OneNetMessageHandler {

    private static final long SAFE_INTEGER_MAX =
            9_007_199_254_740_991L;
    private static final String UUID_V4 =
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$";
    private static final String SHA256 = "^[0-9a-f]{64}$";
    private static final String DEPLOYMENT_CODE =
            "^Dp_[A-Za-z0-9_-]{6,61}$";

    private static final Map<String, EventContract> CONTRACTS = Map.of(
            "configurationProgress",
            new EventContract(
                    "CONFIGURATION_PROGRESS",
                    "RELIABLE_FACT",
                    "CONFIGURATION_APPLICATION"),
            "deviceRuntimeSnapshot",
            new EventContract(
                    "DEVICE_RUNTIME_SNAPSHOT",
                    "TELEMETRY_SNAPSHOT",
                    "DEVICE_DEPLOYMENT"),
            "deviceFaultObserved",
            new EventContract(
                    "DEVICE_FAULT_OBSERVED",
                    "RELIABLE_FACT",
                    "DEVICE_DEPLOYMENT"),
            "deviceFaultRecovered",
            new EventContract(
                    "DEVICE_FAULT_RECOVERED",
                    "RELIABLE_FACT",
                    "DEVICE_DEPLOYMENT"),
            "safetySensorStateChanged",
            new EventContract(
                    "SAFETY_SENSOR_STATE_CHANGED",
                    "RELIABLE_FACT",
                    "DEVICE_DEPLOYMENT"),
            "businessConfirmationReceipt",
            new EventContract(
                    "BUSINESS_CONFIRMATION_RECEIPT",
                    "CONTROL_RECEIPT",
                    "BUSINESS_CONFIRMATION"));

    private static final Map<Long, String> CLOCK_QUALITY = Map.of(
            1L, "SYNCED",
            2L, "ESTIMATED",
            3L, "UNAVAILABLE");
    private static final Map<Long, String> COMPONENT = Map.ofEntries(
            Map.entry(1L, "UART"),
            Map.entry(2L, "DELIVERY_DOOR"),
            Map.entry(3L, "CLEAN_SOLENOID"),
            Map.entry(4L, "WEIGHT_SENSOR"),
            Map.entry(5L, "FULLNESS_SENSOR"),
            Map.entry(6L, "SMOKE_SENSOR"),
            Map.entry(7L, "EDGE_STORAGE"),
            Map.entry(8L, "MCU_STORAGE"),
            Map.entry(9L, "CAMERA"),
            Map.entry(10L, "NETWORK"),
            Map.entry(11L, "CLOCK"),
            Map.entry(12L, "MCU_INTERNAL"));
    private static final Map<Long, String> FAULT_CODE = Map.ofEntries(
            Map.entry(1L, "UART_PROTOCOL"),
            Map.entry(2L, "UART_STORAGE"),
            Map.entry(3L, "DELIVERY_DOOR_OUTPUT_REJECTED"),
            Map.entry(4L, "DELIVERY_DOOR_HIL_NOT_QUALIFIED"),
            Map.entry(5L, "CLEAN_SOLENOID_DRIVER"),
            Map.entry(6L, "WEIGHT_UNSTABLE"),
            Map.entry(7L, "WEIGHT_TIMEOUT"),
            Map.entry(8L, "WEIGHT_SENSOR"),
            Map.entry(9L, "WEIGHT_OVERLOAD"),
            Map.entry(10L, "WEIGHT_PROTOCOL"),
            Map.entry(11L, "WEIGHT_CONFIG"),
            Map.entry(12L, "WEIGHT_DISCONNECTED"),
            Map.entry(13L, "FULLNESS_SENSOR_DIAGNOSTIC"),
            Map.entry(14L, "SMOKE_SENSOR"),
            Map.entry(15L, "MCU_STORAGE"),
            Map.entry(16L, "MCU_INTERNAL"),
            Map.entry(17L, "EDGE_STORAGE"),
            Map.entry(18L, "CAMERA_CAPTURE"),
            Map.entry(19L, "CAMERA_STORAGE"),
            Map.entry(20L, "NETWORK_CONNECTIVITY"),
            Map.entry(21L, "CLOCK_UNSYNCED"));
    private static final Map<Long, String> SENSOR_HEALTH = Map.of(
            1L, "OK",
            2L, "TIMEOUT",
            3L, "SENSOR_FAULT",
            4L, "DISCONNECTED",
            5L, "UNKNOWN",
            6L, "PROTOCOL_ERROR",
            7L, "CONFIG_ERROR",
            8L, "OVERLOAD");

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
            quarantine(
                    productId,
                    hardwareSn,
                    null,
                    rawTransportBody,
                    "UNRESOLVED_SCOPE",
                    "authenticated product is outside the configured runtime epoch");
            throw permanent(
                    "authenticated OneNet product does not match runtime epoch");
        }
        JsonNode params = object(subData, "params");
        for (String identifier : params.propertyNames()) {
            EventContract contract = CONTRACTS.get(identifier);
            if (contract == null) {
                log.warn(
                        "[OneNet·分发] target Adapter rejected identifier={} device={}",
                        safeToken(identifier),
                        safeToken(hardwareSn));
                continue;
            }
            accept(
                    contract,
                    productId,
                    hardwareSn,
                    unwrap(params.get(identifier)),
                    rawTransportBody);
        }
    }

    private void accept(
            EventContract contract,
            String productId,
            String hardwareSn,
            JsonNode wire,
            byte[] rawTransportBody) {
        try {
            Map<String, Object> payload =
                    payload(contract.messageKind(), wire);
            String payloadSha256 = pattern(
                    wire, "payloadSha256", SHA256);
            if (!payloadSha256.equals(
                    OneNetCanonicalJson.payloadSha256(payload))) {
                throw permanent(
                        contract.messageKind()
                                + " payload digest differs");
            }

            Map<String, Object> event = eventEnvelope(
                    contract, wire, payloadSha256, payload);
            validateSemanticShape(contract, event, payload);
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
            UUID commandUid = event.get("commandUid") == null
                    ? null
                    : UUID.fromString((String) event.get("commandUid"));
            String deploymentCode =
                    (String) event.get("deploymentCode");
            TrustedInboxReceipt receipt = trustedInboxPort.receive(
                    new TrustedInboxMessage(
                            "onenet.device-event",
                            OneNetCanonicalJson.stablePrincipalKey(
                                    productId, hardwareSn),
                            eventUid.toString(),
                            contract.messageKind(),
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
                                    hardwareSn, deploymentCode)));
            if (!receipt.transportAcknowledgementAllowed()) {
                throw new IllegalStateException(
                        "reliable inbox did not permit transport ACK");
            }
            log.info(
                    "[OneNet·分发] trusted event durably received kind={} event={} state={}",
                    contract.messageKind(),
                    eventUid,
                    receipt.state());
        } catch (OneNetPermanentMessageException exception) {
            quarantine(
                    productId,
                    hardwareSn,
                    stableEventUid(wire),
                    rawTransportBody,
                    "PERMANENT_FORMAT_ERROR",
                    exception.getMessage());
            throw exception;
        } catch (UntrustedInboxSourceException exception) {
            OneNetPermanentMessageException rejected = permanent(
                    "OneNet device source or target is not authoritative",
                    exception);
            quarantine(
                    productId,
                    hardwareSn,
                    stableEventUid(wire),
                    rawTransportBody,
                    "UNRESOLVED_SCOPE",
                    rejected.getMessage());
            throw rejected;
        } catch (IllegalArgumentException exception) {
            OneNetPermanentMessageException rejected = permanent(
                    contract.messageKind()
                            + " violates target schema",
                    exception);
            quarantine(
                    productId,
                    hardwareSn,
                    stableEventUid(wire),
                    rawTransportBody,
                    "UNSUPPORTED_SCHEMA",
                    rejected.getMessage());
            throw rejected;
        }
    }

    private void quarantine(
            String productId,
            String hardwareSn,
            String externalMessageId,
            byte[] rawTransportBody,
            String reason,
            String diagnostic) {
        trustedInboxPort.quarantine(new TrustedInboxRejection(
                "onenet.device-event",
                OneNetCanonicalJson.stablePrincipalKey(
                        productId, hardwareSn),
                externalMessageId,
                requireRawTransportBody(rawTransportBody),
                reason,
                diagnostic));
    }

    private static String stableEventUid(JsonNode wire) {
        JsonNode value = wire == null ? null : wire.get("eventUid");
        if (value == null || !value.isTextual()
                || !value.asText().matches(UUID_V4)) {
            return null;
        }
        return value.asText();
    }

    private static Map<String, Object> eventEnvelope(
            EventContract contract,
            JsonNode wire,
            String payloadSha256,
            Map<String, Object> payload) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put(
                "schemaVersion",
                exactEnum(wire, "schemaVersion", 1, 1L));
        event.put("eventUid", pattern(wire, "eventUid", UUID_V4));
        event.put(
                "deploymentCode",
                pattern(wire, "deploymentCode", DEPLOYMENT_CODE));
        event.put(
                "edgeEventSequence",
                positiveSafeInteger(wire, "edgeEventSequence"));
        event.put(
                "eventType",
                exactEnum(
                        wire, "eventType", 1, contract.messageKind()));
        event.put(
                "deliveryClass",
                exactEnum(
                        wire,
                        "deliveryClass",
                        1,
                        contract.deliveryClass()));
        JsonNode wireTarget = object(wire, "target");
        Map<String, Object> target = new LinkedHashMap<>();
        target.put(
                "type",
                exactEnum(
                        wireTarget,
                        "type",
                        1,
                        contract.targetType()));
        target.put(
                "uid",
                targetUid(contract.targetType(), wireTarget));
        event.put("target", target);
        event.put(
                "commandUid",
                commandUid(contract.messageKind(), wire));
        event.put("occurredAt", occurredAt(wire));
        event.put(
                "clockQuality",
                enumText(
                        integer(wire, "clockQuality"),
                        CLOCK_QUALITY,
                        "clockQuality"));
        validateClockShape(event);
        event.put("payloadSha256", payloadSha256);
        event.put("payload", payload);
        return event;
    }

    private static String targetUid(
            String targetType, JsonNode target) {
        return switch (targetType) {
            case "DEVICE_DEPLOYMENT" ->
                    pattern(target, "uid", DEPLOYMENT_CODE);
            case "CONFIGURATION_APPLICATION",
                 "BUSINESS_CONFIRMATION" ->
                    pattern(target, "uid", UUID_V4);
            default -> throw permanent(
                    "unsupported trusted event target");
        };
    }

    private static String commandUid(
            String messageKind, JsonNode wire) {
        if ("CONFIGURATION_PROGRESS".equals(messageKind)
                || "BUSINESS_CONFIRMATION_RECEIPT".equals(
                messageKind)) {
            return pattern(wire, "commandUid", UUID_V4);
        }
        return nullablePresenceText(
                wire,
                "commandUidPresent",
                "commandUid",
                UUID_V4,
                64);
    }

    private static Map<String, Object> payload(
            String messageKind, JsonNode wire) {
        return switch (messageKind) {
            case "CONFIGURATION_PROGRESS" ->
                    configurationPayload(wire);
            case "DEVICE_RUNTIME_SNAPSHOT" ->
                    runtimePayload(wire);
            case "DEVICE_FAULT_OBSERVED",
                 "DEVICE_FAULT_RECOVERED" ->
                    faultPayload(wire);
            case "SAFETY_SENSOR_STATE_CHANGED" ->
                    safetyPayload(wire);
            case "BUSINESS_CONFIRMATION_RECEIPT" ->
                    confirmationReceiptPayload(wire);
            default -> throw permanent(
                    "unsupported trusted event payload");
        };
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
                UUID_V4,
                64);
        String errorCode = nullablePresenceText(
                wire,
                "errorCodePresent",
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
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

    private static Map<String, Object> runtimePayload(JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "edgeBootId",
                positiveSafeInteger(wire, "edgeBootId"));
        payload.put("edgeVersion", text(wire, "edgeVersion", 64));
        payload.put(
                "mcuBootId",
                nullablePresenceInteger(
                        wire,
                        "mcuBootIdPresent",
                        "mcuBootId",
                        true));
        payload.put(
                "mcuFirmwareVersion",
                nullablePresenceText(
                        wire,
                        "mcuFirmwareVersionPresent",
                        "mcuFirmwareVersion",
                        "^.{1,64}$",
                        64));
        payload.put(
                "uartState",
                enumText(
                        integer(wire, "uartState"),
                        Map.of(
                                1L, "DISCONNECTED",
                                2L, "NEGOTIATING",
                                3L, "READY",
                                4L, "INCOMPATIBLE",
                                5L, "FAULT"),
                        "uartState"));
        payload.put(
                "uartProtocolMajor",
                nullablePresenceInteger(
                        wire,
                        "uartProtocolMajorPresent",
                        "uartProtocolMajor",
                        false));
        payload.put(
                "uartProtocolMinor",
                nullablePresenceInteger(
                        wire,
                        "uartProtocolMinorPresent",
                        "uartProtocolMinor",
                        false));
        payload.put(
                "capabilityBitmapHex",
                pattern(
                        wire,
                        "capabilityBitmapHex",
                        "^[0-9a-f]{16}$"));
        boolean appliedPresent = bool(wire, "appliedConfigPresent");
        JsonNode applied = wire.get("appliedConfig");
        if (appliedPresent) {
            if (applied == null || !applied.isObject()) {
                throw permanent(
                        "appliedConfig must exist when marked present");
            }
            Map<String, Object> config = new LinkedHashMap<>();
            config.put(
                    "version",
                    positiveSafeInteger(applied, "version"));
            config.put(
                    "contentSha256",
                    pattern(applied, "contentSha256", SHA256));
            config.put(
                    "mcuPayloadSha256",
                    pattern(applied, "mcuPayloadSha256", SHA256));
            payload.put("appliedConfig", config);
        } else {
            payload.put("appliedConfig", null);
        }
        payload.put(
                "localStorageState",
                enumText(
                        integer(wire, "localStorageState"),
                        Map.of(
                                1L, "HEALTHY",
                                2L, "DEGRADED",
                                3L, "READ_ONLY",
                                4L, "FULL",
                                5L, "CORRUPT"),
                        "localStorageState"));
        payload.put(
                "clockState",
                enumText(
                        integer(wire, "clockState"),
                        CLOCK_QUALITY,
                        "clockState"));
        payload.put(
                "pendingReliableEventCount",
                nonNegativeSafeInteger(
                        wire, "pendingReliableEventCount"));
        JsonNode ports = wire.get("ports");
        if (ports == null || !ports.isArray() || ports.isEmpty()) {
            throw permanent("runtime ports must be a non-empty array");
        }
        List<Map<String, Object>> normalizedPorts = new ArrayList<>();
        for (JsonNode port : ports) {
            normalizedPorts.add(runtimePort(port));
        }
        payload.put("ports", normalizedPorts);
        return payload;
    }

    private static Map<String, Object> runtimePort(JsonNode wire) {
        Map<String, Object> port = new LinkedHashMap<>();
        port.put("portNo", positiveSafeInteger(wire, "portNo"));
        port.put(
                "lastDeliveryDoorCommand",
                enumText(
                        integer(wire, "lastDeliveryDoorCommand"),
                        Map.of(
                                1L, "NONE",
                                2L, "OPEN",
                                3L, "CLOSE"),
                        "lastDeliveryDoorCommand"));
        port.put(
                "lastDeliveryDoorOutputStatus",
                enumText(
                        integer(wire, "lastDeliveryDoorOutputStatus"),
                        Map.of(
                                1L, "NOT_DISPATCHED",
                                2L, "COMMAND_DISPATCHED",
                                3L, "COMMAND_SUPERSEDED_BEFORE_DISPATCH",
                                4L, "COALESCED_WITH_EXISTING_CLOSE",
                                5L, "OUTPUT_REJECTED"),
                        "lastDeliveryDoorOutputStatus"));
        port.put(
                "deliveryDoorPhysicalStateBasis",
                exactEnum(
                        wire,
                        "deliveryDoorPhysicalStateBasis",
                        1,
                        "NOT_OBSERVABLE"));
        port.put(
                "cleanLockPowerState",
                enumText(
                        integer(wire, "cleanLockPowerState"),
                        Map.of(
                                1L, "ENERGIZED",
                                2L, "DEENERGIZED",
                                3L, "UNKNOWN"),
                        "cleanLockPowerState"));
        port.put(
                "solenoidHealth",
                enumText(
                        integer(wire, "solenoidHealth"),
                        Map.of(
                                1L, "OK",
                                2L, "DRIVER_FAULT",
                                3L, "DISCONNECTED",
                                4L, "UNKNOWN"),
                        "solenoidHealth"));
        port.put(
                "cleanDoorStateBasis",
                enumText(
                        integer(wire, "cleanDoorStateBasis"),
                        Map.of(
                                1L, "NOT_OBSERVABLE",
                                2L, "CLEANER_CONFIRMATION"),
                        "cleanDoorStateBasis"));
        port.put(
                "cleanerPhysicalCloseConfirmed",
                bool(wire, "cleanerPhysicalCloseConfirmed"));
        port.put(
                "weightMeasurementUid",
                nullablePresenceText(
                        wire,
                        "weightMeasurementUidPresent",
                        "weightMeasurementUid",
                        UUID_V4,
                        64));
        String measurementStatus = enumText(
                integer(wire, "weightMeasurementStatus"),
                Map.of(
                        1L, "STABLE",
                        2L, "UNSTABLE",
                        3L, "TIMEOUT",
                        4L, "SENSOR_FAULT",
                        5L, "OVERLOAD",
                        6L, "PROTOCOL_ERROR",
                        7L, "CONFIG_ERROR",
                        8L, "DISCONNECTED"),
                "weightMeasurementStatus");
        port.put("weightMeasurementStatus", measurementStatus);
        boolean valueAvailable = bool(
                wire, "weightValueAvailable");
        port.put("weightValueAvailable", valueAvailable);
        port.put(
                "reportedWeightGrams",
                nullablePresenceSignedInteger(
                        wire,
                        "reportedWeightGramsPresent",
                        "reportedWeightGrams"));
        String valueKind = enumText(
                integer(wire, "weightValueKind"),
                Map.of(
                        1L, "NONE",
                        2L, "STABLE_WINDOW_MEAN",
                        3L, "LAST_FOUR_MEAN",
                        4L, "AVAILABLE_SAMPLES_MEAN",
                        5L, "LAST_OBSERVED"),
                "weightValueKind");
        port.put("weightValueKind", valueKind);
        port.put(
                "measurementElapsedMs",
                nonNegativeSafeInteger(wire, "measurementElapsedMs"));
        port.put(
                "weightSampleCount",
                nonNegativeSafeInteger(wire, "weightSampleCount"));
        port.put(
                "calibrationVersion",
                nonNegativeSafeInteger(wire, "calibrationVersion"));
        port.put(
                "weightSensorHealth",
                enumText(
                        integer(wire, "weightSensorHealth"),
                        SENSOR_HEALTH,
                        "weightSensorHealth"));
        port.put(
                "weightFaultCode",
                nullablePresenceEnum(
                        wire,
                        "weightFaultCodePresent",
                        "weightFaultCode",
                        FAULT_CODE));
        port.put(
                "weightMcuBootId",
                nullablePresenceInteger(
                        wire,
                        "weightMcuBootIdPresent",
                        "weightMcuBootId",
                        true));
        port.put(
                "weightMcuEventSequence",
                nullablePresenceInteger(
                        wire,
                        "weightMcuEventSequencePresent",
                        "weightMcuEventSequence",
                        true));
        port.put(
                "fullnessSensorKind",
                enumText(
                        integer(wire, "fullnessSensorKind"),
                        Map.of(
                                1L, "ULTRASONIC",
                                2L, "DIGITAL_INFRARED"),
                        "fullnessSensorKind"));
        port.put(
                "fullnessSensorValue",
                enumText(
                        integer(wire, "fullnessSensorValue"),
                        Map.of(
                                1L, "CLEAR",
                                2L, "BLOCKED"),
                        "fullnessSensorValue"));
        port.put(
                "fullnessSampleBasis",
                enumText(
                        integer(wire, "fullnessSampleBasis"),
                        Map.of(
                                1L, "MEASURED_MEDIAN",
                                2L, "NO_ECHO_CLEAR_FALLBACK",
                                3L,
                                "INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK",
                                4L, "NOT_SAMPLED"),
                        "fullnessSampleBasis"));
        port.put(
                "representativeDistanceMm",
                nullablePresenceInteger(
                        wire,
                        "representativeDistanceMmPresent",
                        "representativeDistanceMm",
                        false));
        port.put(
                "fullnessValidSampleCount",
                nonNegativeSafeInteger(
                        wire, "fullnessValidSampleCount"));
        port.put(
                "smokeState",
                enumText(
                        integer(wire, "smokeState"),
                        Map.of(
                                1L, "NORMAL",
                                2L, "ALARM",
                                3L, "UNKNOWN"),
                        "smokeState"));
        port.put(
                "smokeSensorHealth",
                enumText(
                        integer(wire, "smokeSensorHealth"),
                        SENSOR_HEALTH,
                        "smokeSensorHealth"));
        port.put(
                "faultBitmap",
                nonNegativeSafeInteger(wire, "faultBitmap"));
        if (valueAvailable
                != (port.get("reportedWeightGrams") != null)
                || (!valueAvailable && !"NONE".equals(valueKind))
                || ("STABLE".equals(measurementStatus)
                && (!valueAvailable
                || !"STABLE_WINDOW_MEAN".equals(valueKind)))) {
            throw permanent(
                    "runtime weight availability fields differ");
        }
        boolean closeConfirmed =
                (Boolean) port.get("cleanerPhysicalCloseConfirmed");
        if (closeConfirmed != "CLEANER_CONFIRMATION".equals(
                port.get("cleanDoorStateBasis"))) {
            throw permanent(
                    "runtime cleaner confirmation fields differ");
        }
        return port;
    }

    private static Map<String, Object> faultPayload(JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "faultUid",
                pattern(wire, "faultUid", UUID_V4));
        payload.put(
                "component",
                enumText(
                        integer(wire, "component"),
                        COMPONENT,
                        "component"));
        payload.put(
                "faultCode",
                enumText(
                        integer(wire, "faultCode"),
                        FAULT_CODE,
                        "faultCode"));
        payload.put(
                "severity",
                enumText(
                        integer(wire, "severity"),
                        Map.of(
                                1L, "WARNING",
                                2L, "BLOCK_PORT",
                                3L, "BLOCK_DEVICE"),
                        "severity"));
        payload.put(
                "portNo",
                nullablePresenceInteger(
                        wire,
                        "portNoPresent",
                        "portNo",
                        true));
        payload.put(
                "mcuBootId",
                nullablePresenceInteger(
                        wire,
                        "mcuBootIdPresent",
                        "mcuBootId",
                        true));
        payload.put(
                "mcuEventSequence",
                nullablePresenceInteger(
                        wire,
                        "mcuEventSequencePresent",
                        "mcuEventSequence",
                        true));
        return payload;
    }

    private static Map<String, Object> safetyPayload(JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "mcuBootId",
                positiveSafeInteger(wire, "mcuBootId"));
        payload.put(
                "mcuEventSequence",
                positiveSafeInteger(wire, "mcuEventSequence"));
        payload.put(
                "portNo",
                nullablePresenceInteger(
                        wire,
                        "portNoPresent",
                        "portNo",
                        true));
        payload.put(
                "smokeState",
                enumText(
                        integer(wire, "smokeState"),
                        Map.of(
                                1L, "NORMAL",
                                2L, "ALARM",
                                3L, "UNKNOWN"),
                        "smokeState"));
        payload.put(
                "smokeSensorHealth",
                enumText(
                        integer(wire, "smokeSensorHealth"),
                        SENSOR_HEALTH,
                        "smokeSensorHealth"));
        payload.put(
                "faultCode",
                nullablePresenceEnum(
                        wire,
                        "faultCodePresent",
                        "faultCode",
                        FAULT_CODE));
        String workType = enumText(
                integer(wire, "workType"),
                Map.of(
                        1L, "NONE",
                        2L, "CONFIG_APPLICATION",
                        3L, "DELIVERY_SESSION",
                        4L, "CLEAN_OPERATION",
                        5L, "FULLNESS_DETECTION",
                        6L, "BASELINE_MEASUREMENT"),
                "workType");
        String workUid = nullablePresenceText(
                wire,
                "workUidPresent",
                "workUid",
                UUID_V4,
                64);
        if ("NONE".equals(workType) != (workUid == null)) {
            throw permanent("safety work identity fields differ");
        }
        payload.put("workType", workType);
        payload.put("workUid", workUid);
        return payload;
    }

    private static Map<String, Object> confirmationReceiptPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "confirmationUid",
                pattern(wire, "confirmationUid", UUID_V4));
        payload.put(
                "originalEventUid",
                pattern(wire, "originalEventUid", UUID_V4));
        payload.put(
                "originalPayloadSha256",
                pattern(wire, "originalPayloadSha256", SHA256));
        payload.put(
                "outcome",
                enumText(
                        integer(wire, "outcome"),
                        Map.of(
                                1L, "BUSINESS_APPLIED",
                                2L, "EVENT_QUARANTINED"),
                        "outcome"));
        return payload;
    }

    private static void validateSemanticShape(
            EventContract contract,
            Map<String, Object> event,
            Map<String, Object> payload) {
        @SuppressWarnings("unchecked")
        Map<String, Object> target =
                (Map<String, Object>) event.get("target");
        String targetUid = (String) target.get("uid");
        String deploymentCode =
                (String) event.get("deploymentCode");
        if ("DEVICE_DEPLOYMENT".equals(contract.targetType())
                && !deploymentCode.equals(targetUid)) {
            throw permanent(
                    "deployment event target differs from envelope");
        }
        if ("CONFIGURATION_PROGRESS".equals(contract.messageKind())
                && !payload.get("applicationUid").equals(targetUid)) {
            throw permanent(
                    "configuration application target differs from payload");
        }
        if ("BUSINESS_CONFIRMATION_RECEIPT".equals(
                contract.messageKind())
                && !payload.get("confirmationUid").equals(targetUid)) {
            throw permanent(
                    "confirmation receipt target differs from payload");
        }
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
            String pattern,
            int maximumLength) {
        boolean present = bool(node, presenceField);
        JsonNode raw = node.get(valueField);
        if (!present) {
            if (raw != null && !raw.isNull()
                    && (!raw.isTextual()
                    || !raw.asText().isEmpty())) {
                throw permanent(
                        valueField
                                + " exists while presence flag is false");
            }
            return null;
        }
        String value = text(node, valueField, maximumLength);
        if (!value.matches(pattern)) {
            throw permanent(valueField + " has an invalid format");
        }
        return value;
    }

    private static Long nullablePresenceInteger(
            JsonNode node,
            String presenceField,
            String valueField,
            boolean positive) {
        boolean present = bool(node, presenceField);
        JsonNode raw = node.get(valueField);
        if (raw != null && !raw.isIntegralNumber()) {
            throw permanent(valueField + " must be an integer");
        }
        if (!present) {
            return null;
        }
        long value = integer(node, valueField);
        if ((positive && value <= 0)
                || (!positive && value < 0)
                || value > SAFE_INTEGER_MAX) {
            throw permanent(valueField + " is outside the safe range");
        }
        return value;
    }

    private static Long nullablePresenceSignedInteger(
            JsonNode node,
            String presenceField,
            String valueField) {
        boolean present = bool(node, presenceField);
        JsonNode raw = node.get(valueField);
        if (raw != null && !raw.isIntegralNumber()) {
            throw permanent(valueField + " must be an integer");
        }
        if (!present) {
            return null;
        }
        long value = integer(node, valueField);
        if (value < -SAFE_INTEGER_MAX || value > SAFE_INTEGER_MAX) {
            throw permanent(valueField + " is outside the safe range");
        }
        return value;
    }

    private static String nullablePresenceEnum(
            JsonNode node,
            String presenceField,
            String valueField,
            Map<Long, String> mapping) {
        boolean present = bool(node, presenceField);
        JsonNode raw = node.get(valueField);
        if (raw != null && !raw.isIntegralNumber()) {
            throw permanent(valueField + " must be an integer");
        }
        if (!present) {
            return null;
        }
        return enumText(
                integer(node, valueField), mapping, valueField);
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

    private static long nonNegativeSafeInteger(
            JsonNode node, String field) {
        long value = integer(node, field);
        if (value < 0 || value > SAFE_INTEGER_MAX) {
            throw permanent(field + " is outside the safe range");
        }
        return value;
    }

    private static long integer(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw permanent(field + " must be an integer");
        }
        return value.longValue();
    }

    private static boolean bool(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
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

    private record EventContract(
            String messageKind,
            String deliveryClass,
            String targetType) {
    }
}
