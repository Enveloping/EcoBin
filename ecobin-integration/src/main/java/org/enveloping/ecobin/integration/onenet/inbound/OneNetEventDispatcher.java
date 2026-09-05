package org.enveloping.ecobin.integration.onenet.inbound;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.enveloping.ecobin.integration.cos.CosProperties;
import org.enveloping.ecobin.integration.onenet.outbound.OneNetProperties;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxExecutionLane;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxMessage;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxPort;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxReceipt;
import org.enveloping.ecobin.operations.api.inbox.TrustedInboxRejection;
import org.slf4j.MDC;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * OneNet 北向消息进入后端的可信适配器。
 *
 * <p>这里先核对 OneNet 产品和设备身份，再把线级数字枚举转换成稳定业务语义，校验载荷
 * 摘要与事件摘要，最后写入可靠收件箱。方法正常返回只表示消息已经可靠落库，Pulsar 可以
 * ACK；它不表示投递订单等业务事实已经处理完成。</p>
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

    private static final Map<String, EventContract> CONTRACTS =
            Map.ofEntries(
            Map.entry("configurationProgress",
            new EventContract(
                    "CONFIGURATION_PROGRESS",
                    "RELIABLE_FACT",
                    "CONFIGURATION_APPLICATION")),
            Map.entry("deviceCommandObserved",
            new EventContract(
                    "DEVICE_COMMAND_OBSERVED",
                    "RELIABLE_FACT",
                    "DEVICE_COMMAND")),
            Map.entry("deliveryComplete",
            new EventContract(
                    "DELIVERY_COMPLETE",
                    "RELIABLE_FACT",
                    "DELIVERY_SESSION")),
            Map.entry("cleanComplete",
            new EventContract(
                    "CLEAN_COMPLETE",
                    "RELIABLE_FACT",
                    "CLEAN_OPERATION")),
            Map.entry("fullnessStateChanged",
            new EventContract(
                    "FULLNESS_STATE_CHANGED",
                    "RELIABLE_FACT",
                    "PORT_FULLNESS_STATE")),
            Map.entry("fullnessSampleComplete",
            new EventContract(
                    "FULLNESS_SAMPLE_COMPLETE",
                    "RELIABLE_FACT",
                    "FULLNESS_DETECTION")),
            Map.entry("baselineMeasurementComplete",
            new EventContract(
                    "BASELINE_MEASUREMENT_COMPLETE",
                    "RELIABLE_FACT",
                    "BASELINE_MEASUREMENT")),
            Map.entry("deviceRuntimeSnapshot",
            new EventContract(
                    "DEVICE_RUNTIME_SNAPSHOT",
                    "TELEMETRY_SNAPSHOT",
                    "DEVICE_ASSET")),
            Map.entry("deviceSoftwareStateReported",
            new EventContract(
                    "DEVICE_SOFTWARE_STATE_REPORTED",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("deviceFaultObserved",
            new EventContract(
                    "DEVICE_FAULT_OBSERVED",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("deviceFaultRecovered",
            new EventContract(
                    "DEVICE_FAULT_RECOVERED",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("safetySensorStateChanged",
            new EventContract(
                    "SAFETY_SENSOR_STATE_CHANGED",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("deviceAcceptanceEvidence",
            new EventContract(
                    "DEVICE_ACCEPTANCE_EVIDENCE",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("photoStatusReported",
            new EventContract(
                    "PHOTO_STATUS_REPORTED",
                    "RELIABLE_FACT",
                    "WORK")),
            Map.entry("photoUploadGrantRequested",
            new EventContract(
                    "PHOTO_UPLOAD_GRANT_REQUESTED",
                    "RELIABLE_FACT",
                    "WORK")),
            Map.entry("businessConfirmationReceipt",
            new EventContract(
                    "BUSINESS_CONFIRMATION_RECEIPT",
                    "CONTROL_RECEIPT",
                    "BUSINESS_CONFIRMATION")),
            Map.entry("remoteSupportTunnelStatus",
            new EventContract(
                    "REMOTE_SUPPORT_TUNNEL_STATUS",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")),
            Map.entry("mcuFirmwareUpdateProgress",
            new EventContract(
                    "MCU_FIRMWARE_UPDATE_PROGRESS",
                    "RELIABLE_FACT",
                    "MCU_FIRMWARE_DEPLOYMENT")),
            Map.entry("businessRuntimeUpdateProgress",
            new EventContract(
                    "BUSINESS_RUNTIME_UPDATE_PROGRESS",
                    "RELIABLE_FACT",
                    "BUSINESS_RUNTIME_DEPLOYMENT")),
            Map.entry("businessRuntimeCancelResult",
            new EventContract(
                    "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT",
                    "RELIABLE_FACT",
                    "BUSINESS_RUNTIME_DEPLOYMENT")),
            Map.entry("factorySealCompleted",
            new EventContract(
                    "FACTORY_SEAL_COMPLETED",
                    "RELIABLE_FACT",
                    "DEVICE_ASSET")));

    private static final Map<Long, String> CLOCK_QUALITY = Map.of(
            1L, "SYNCED",
            2L, "ESTIMATED",
            3L, "UNAVAILABLE");
    private static final Map<Long, String> EDGE_PROTOCOL_VERSION = Map.of(
            1L, "2");
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
    private static final Map<Long, String> MEASUREMENT_STATUS = Map.of(
            1L, "STABLE",
            2L, "UNSTABLE",
            3L, "TIMEOUT",
            4L, "SENSOR_FAULT",
            5L, "OVERLOAD",
            6L, "PROTOCOL_ERROR",
            7L, "CONFIG_ERROR",
            8L, "DISCONNECTED");
    private static final Map<Long, String> WEIGHT_VALUE_KIND = Map.of(
            1L, "NONE",
            2L, "STABLE_WINDOW_MEAN",
            3L, "LAST_FOUR_MEAN",
            4L, "AVAILABLE_SAMPLES_MEAN",
            5L, "LAST_OBSERVED");
    private static final Set<String> DELIVERY_PHOTO_SLOTS = Set.of(
            "BEFORE_INNER",
            "BEFORE_OUTER",
            "AFTER_INNER",
            "AFTER_OUTER");
    private static final Set<String> CLEAN_PHOTO_SLOTS = Set.of(
            "FIRST_OPEN_INNER",
            "FIRST_OPEN_OUTER",
            "FINAL_CLOSE_INNER",
            "FINAL_CLOSE_OUTER");
    private static final Set<String> MEASUREMENT_FAULT_CODES = Set.of(
            "UART_PROTOCOL",
            "UART_STORAGE",
            "DELIVERY_DOOR_OUTPUT_REJECTED",
            "DELIVERY_DOOR_HIL_NOT_QUALIFIED",
            "CLEAN_SOLENOID_DRIVER",
            "WEIGHT_UNSTABLE",
            "WEIGHT_TIMEOUT",
            "WEIGHT_SENSOR",
            "WEIGHT_OVERLOAD",
            "WEIGHT_PROTOCOL",
            "WEIGHT_CONFIG",
            "WEIGHT_DISCONNECTED",
            "FULLNESS_SENSOR_DIAGNOSTIC",
            "SMOKE_SENSOR",
            "MCU_STORAGE",
            "MCU_INTERNAL");
    private static final Set<String> PERMANENT_ASSET_FACTS = Set.of(
            "DEVICE_RUNTIME_SNAPSHOT",
            "DEVICE_FAULT_OBSERVED",
            "DEVICE_FAULT_RECOVERED",
            "SAFETY_SENSOR_STATE_CHANGED",
            "REMOTE_SUPPORT_TUNNEL_STATUS",
            "MCU_FIRMWARE_UPDATE_PROGRESS",
            "FACTORY_SEAL_COMPLETED");

    private final TrustedInboxPort trustedInboxPort;
    private final TrustedDeviceSourceScopePort sourceScopePort;
    private final OneNetProperties oneNetProperties;
    private final CosProperties cosProperties;
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
        if ("deviceOnline".equals(msgType)
                || "deviceOffline".equals(msgType)) {
            // 只有 OneNet 生命周期通知能改变资产级在线事实。普通业务事件和运行快照
            // 即使刚刚到达，也不能据此推断设备仍在线。
            acceptTransportPresence(
                    root,
                    msgType,
                    mqMessageId,
                    rawTransportBody);
            return;
        }
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
        try (MDC.MDCCloseable ignoredDevice = MDC.putCloseable(
                "hardwareSn", safeToken(hardwareSn))) {
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
    }

    private void acceptTransportPresence(
            JsonNode root,
            String msgType,
            String mqMessageId,
            byte[] rawTransportBody) {
        String productId = null;
        String hardwareSn = null;
        try {
            JsonNode subData = object(root, "subData");
            hardwareSn = text(subData, "deviceName", 64);
            productId = text(subData, "productId", 128);
            if (oneNetProperties.getProductId() == null
                    || !oneNetProperties.getProductId().equals(productId)) {
                throw permanent(
                        "authenticated OneNet product does not match runtime epoch");
            }
            JsonNode time = subData.get("time");
            if (time == null || !time.canConvertToLong()
                    || time.asLong() <= 0) {
                throw permanent(
                        "OneNet lifecycle time must be a positive epoch millisecond");
            }
            String externalMessageId = boundedMqMessageId(mqMessageId);
            Map<String, Object> source = new LinkedHashMap<>();
            source.put("productId", productId);
            source.put("deviceName", hardwareSn);
            Map<String, Object> presence = new LinkedHashMap<>();
            presence.put(
                    "status",
                    "deviceOnline".equals(msgType)
                            ? "ONLINE"
                            : "OFFLINE");
            presence.put(
                    "observedAt",
                    Instant.ofEpochMilli(time.asLong()).toString());
            Map<String, Object> normalized = new LinkedHashMap<>();
            normalized.put("trustedSource", source);
            normalized.put("presence", presence);
            try (MDC.MDCCloseable ignoredDevice = MDC.putCloseable(
                    "hardwareSn", safeToken(hardwareSn))) {
                TrustedInboxReceipt receipt = trustedInboxPort.receive(
                        new TrustedInboxMessage(
                            "onenet.device-lifecycle",
                            OneNetCanonicalJson.stablePrincipalKey(
                                    productId, hardwareSn),
                            externalMessageId,
                            "DEVICE_TRANSPORT_STATUS_CHANGED",
                            1,
                            requireRawTransportBody(rawTransportBody),
                            objectMapper.writeValueAsString(normalized),
                            "ONENET_PULSAR_AES",
                            "product:" + productId
                                    + ";device:" + hardwareSn,
                            null,
                            null,
                            TrustedInboxExecutionLane.DEVICE,
                                sourceScopePort.resolverForPlatformAsset(
                                        hardwareSn)));
                if (!receipt.transportAcknowledgementAllowed()) {
                    throw new IllegalStateException(
                            "reliable inbox did not permit lifecycle ACK");
                }
                log.info(
                        "[OneNet·分发] lifecycle durably received status={} device={} state={}",
                        presence.get("status"),
                        safeToken(hardwareSn),
                        receipt.state());
            }
        } catch (OneNetPermanentMessageException exception) {
            quarantinePresence(
                    productId,
                    hardwareSn,
                    mqMessageId,
                    rawTransportBody,
                    "PERMANENT_FORMAT_ERROR",
                    exception.getMessage());
            throw exception;
        } catch (UntrustedInboxSourceException exception) {
            OneNetPermanentMessageException rejected = permanent(
                    "OneNet lifecycle device is not registered",
                    exception);
            quarantinePresence(
                    productId,
                    hardwareSn,
                    mqMessageId,
                    rawTransportBody,
                    "UNRESOLVED_SCOPE",
                    rejected.getMessage());
            throw rejected;
        }
    }

    private void quarantinePresence(
            String productId,
            String hardwareSn,
            String externalMessageId,
            byte[] rawTransportBody,
            String reason,
            String diagnostic) {
        trustedInboxPort.quarantine(new TrustedInboxRejection(
                "onenet.device-lifecycle",
                OneNetCanonicalJson.stablePrincipalKey(
                        productId == null ? "unknown" : productId,
                        hardwareSn == null ? "unknown" : hardwareSn),
                boundedMqMessageId(externalMessageId),
                requireRawTransportBody(rawTransportBody),
                reason,
                diagnostic));
    }

    private static String boundedMqMessageId(String value) {
        if (value == null || value.isBlank()) {
            throw permanent("OneNet MQ message id is missing");
        }
        if (value.length() > 160) {
            try {
                byte[] digest = MessageDigest.getInstance("SHA-256")
                        .digest(value.getBytes(StandardCharsets.UTF_8));
                return "sha256:" + HexFormat.of().formatHex(digest);
            } catch (NoSuchAlgorithmException exception) {
                throw new IllegalStateException(
                        "JVM does not provide SHA-256", exception);
            }
        }
        return value;
    }

    private void accept(
            EventContract contract,
            String productId,
            String hardwareSn,
            JsonNode wire,
            byte[] rawTransportBody) {
        try {
            // 先从 OneNet 线级字段恢复语义载荷，再用设备提供的摘要复算。
            // 同一 eventUid 携带不同内容会在收件箱层被隔离，不能覆盖原设备事实。
            Map<String, Object> payload =
                    payload(
                            contract.messageKind(),
                            wire,
                            cosProperties.getBaseUrl());
            String payloadSha256 = pattern(
                    wire, "payloadSha256", SHA256);
            if (!payloadSha256.equals(
                    OneNetCanonicalJson.payloadSha256(payload))) {
                throw permanent(
                        contract.messageKind()
                                + " payload digest differs");
            }

            Map<String, Object> event = eventEnvelope(
                    contract, wire, hardwareSn, payloadSha256, payload);
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
            try (MDC.MDCCloseable ignoredEvent = MDC.putCloseable(
                         "eventUid", eventUid.toString())) {
                // receive() 使用独立短事务同时写收件箱和处理任务。只有它允许 ACK，
                // 外层 Pulsar 消费者才确认传输消息，避免“先 ACK、后端宕机、事实丢失”。
                TrustedInboxReceipt receipt = trustedInboxPort.receive(
                        new TrustedInboxMessage(
                            sourceNamespace(contract, payload),
                            OneNetCanonicalJson.stablePrincipalKey(
                                    productId, hardwareSn),
                            eventUid.toString(),
                            contract.messageKind(),
                            2,
                            requireRawTransportBody(rawTransportBody),
                            objectMapper.writeValueAsString(normalized),
                            "ONENET_PULSAR_AES",
                            "product:" + productId
                                    + ";device:" + hardwareSn,
                            eventUid,
                            commandUid,
                            TrustedInboxExecutionLane.DEVICE,
                            sourceScopeResolver(
                                    contract,
                                    hardwareSn,
                                    event,
                                    payload)));
                if (!receipt.transportAcknowledgementAllowed()) {
                    throw new IllegalStateException(
                            "reliable inbox did not permit transport ACK");
                }
                log.info(
                        "[OneNet·分发] trusted event durably received kind={} event={} state={}",
                        contract.messageKind(),
                        eventUid,
                        receipt.state());
            }
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

    private TrustedInboxScopeResolver sourceScopeResolver(
            EventContract contract,
            String hardwareSn,
            Map<String, Object> event,
            Map<String, Object> payload) {
        if ("DEVICE_COMMAND_OBSERVED".equals(
                contract.messageKind())
                && "AUTHORIZE_FACTORY_SEAL".equals(
                payload.get("observedCommandType"))) {
            return sourceScopePort.resolverForPlatformAsset(hardwareSn);
        }
        if ("DEVICE_ACCEPTANCE_EVIDENCE".equals(
                contract.messageKind())
                || "REMOTE_SUPPORT_TUNNEL_STATUS".equals(
                        contract.messageKind())
                || "FACTORY_SEAL_COMPLETED".equals(
                        contract.messageKind())
                || "DEVICE_SOFTWARE_STATE_REPORTED".equals(
                        contract.messageKind())
                || "MCU_FIRMWARE_UPDATE_PROGRESS".equals(
                        contract.messageKind())
                || "BUSINESS_RUNTIME_UPDATE_PROGRESS".equals(
                        contract.messageKind())
                || "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT".equals(
                        contract.messageKind())) {
            return sourceScopePort.resolverForPlatformAsset(hardwareSn);
        }
        if ("BUSINESS_CONFIRMATION_RECEIPT".equals(
                contract.messageKind())) {
            return sourceScopePort.resolverForBusinessConfirmation(
                    hardwareSn,
                    (String) payload.get("confirmationUid"));
        }
        if (PERMANENT_ASSET_FACTS.contains(contract.messageKind())) {
            String occurredAt = (String) event.get("occurredAt");
            Instant trustedOccurrence = occurredAt == null
                    ? null : Instant.parse(occurredAt);
            if (!"SYNCED".equals(event.get("clockQuality"))) {
                trustedOccurrence = null;
            } else if (trustedOccurrence != null
                    && trustedOccurrence.isAfter(
                            Instant.now().plusSeconds(60))) {
                log.warn(
                        "Ignoring future device occurrence for ownership "
                                + "scope hardwareSn={} eventUid={} "
                                + "maximumFutureSkewSeconds=60",
                        hardwareSn, event.get("eventUid"));
                trustedOccurrence = null;
            }
            return sourceScopePort.resolverForPermanentAssetFact(
                    hardwareSn, trustedOccurrence);
        }
        return sourceScopePort.resolverForOrganizationAsset(hardwareSn);
    }

    private static String sourceNamespace(
            EventContract contract,
            Map<String, Object> payload) {
        // 远程维护状态属于平台控制事实。独立的稳定来源身份既隔离权限边界，也允许
        // 已按旧规则错误落入机构作用域的不可变收件记录通过重传安全收敛。
        return switch (contract.messageKind()) {
            case "DEVICE_COMMAND_OBSERVED" ->
                    "AUTHORIZE_FACTORY_SEAL".equals(
                            payload.get("observedCommandType"))
                            ? "onenet.factory-seal-observation"
                            : "onenet.device-event";
            case "REMOTE_SUPPORT_TUNNEL_STATUS" ->
                    "onenet.remote-support-status";
            case "MCU_FIRMWARE_UPDATE_PROGRESS" ->
                    "onenet.mcu-firmware-progress";
            case "BUSINESS_RUNTIME_UPDATE_PROGRESS" ->
                    "onenet.business-runtime-progress";
            case "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT" ->
                    "onenet.business-runtime-cancel-result";
            case "DEVICE_SOFTWARE_STATE_REPORTED" ->
                    "onenet.device-software-state";
            case "FACTORY_SEAL_COMPLETED" ->
                    "onenet.factory-seal-completion";
            default -> "onenet.device-event";
        };
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
            String trustedHardwareSn,
            String payloadSha256,
            Map<String, Object> payload) {
        Map<String, Object> event = new LinkedHashMap<>();
        event.put(
                "schemaVersion",
                exactEnum(wire, "schemaVersion", 1, 2L));
        event.put("eventUid", pattern(wire, "eventUid", UUID_V4));
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
        String targetType = targetType(contract, wire, payload);
        Map<String, Object> target = new LinkedHashMap<>();
        target.put(
                "type",
                exactEnum(
                        wireTarget,
                        "type",
                        "WORK".equals(contract.targetType())
                                ? integer(wire, "workType")
                                : 1,
                        targetType));
        target.put(
                "uid",
                targetUid(targetType, wireTarget, trustedHardwareSn));
        event.put("target", target);
        event.put(
                "commandUid",
                commandUid(contract.messageKind(), wire));
        event.put(
                "occurredAt",
                occurredAt(contract.messageKind(), wire));
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
            String targetType,
            JsonNode target,
            String trustedHardwareSn) {
        return switch (targetType) {
            case "DEVICE_ASSET" -> trustedHardwareSn;
            case "CONFIGURATION_APPLICATION",
                 "BUSINESS_CONFIRMATION",
                 "DEVICE_COMMAND",
                 "DELIVERY_SESSION",
                 "CLEAN_OPERATION",
                 "FULLNESS_DETECTION",
                 "BASELINE_MEASUREMENT",
                 "PORT_FULLNESS_STATE",
                 "MCU_FIRMWARE_DEPLOYMENT",
                 "BUSINESS_RUNTIME_DEPLOYMENT" ->
                    pattern(target, "uid", UUID_V4);
            default -> throw permanent(
                    "unsupported trusted event target");
        };
    }

    private static String targetType(
            EventContract contract,
            JsonNode wire,
            Map<String, Object> payload) {
        if (!"WORK".equals(contract.targetType())) {
            return contract.targetType();
        }
        String workType = enumText(
                integer(wire, "workType"),
                Map.of(
                        1L, "DELIVERY_SESSION",
                        2L, "CLEAN_OPERATION"),
                "workType");
        if (!workType.equals(payload.get("workType"))) {
            throw permanent(
                    "work target type differs from payload");
        }
        return workType;
    }

    private static String commandUid(
            String messageKind, JsonNode wire) {
        if ("CONFIGURATION_PROGRESS".equals(messageKind)
                || "DEVICE_COMMAND_OBSERVED".equals(messageKind)
                || "BUSINESS_CONFIRMATION_RECEIPT".equals(
                messageKind)
                || "DELIVERY_COMPLETE".equals(messageKind)
                || "CLEAN_COMPLETE".equals(messageKind)
                || "FULLNESS_SAMPLE_COMPLETE".equals(
                messageKind)
                || "BASELINE_MEASUREMENT_COMPLETE".equals(
                messageKind)
                || "DEVICE_ACCEPTANCE_EVIDENCE".equals(
                messageKind)
                || "REMOTE_SUPPORT_TUNNEL_STATUS".equals(
                messageKind)
                || "BUSINESS_RUNTIME_UPDATE_PROGRESS".equals(
                messageKind)
                || "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT".equals(
                messageKind)
                || "FACTORY_SEAL_COMPLETED".equals(messageKind)) {
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
            String messageKind,
            JsonNode wire,
            String trustedCosBaseUrl) {
        return switch (messageKind) {
            case "CONFIGURATION_PROGRESS" ->
                    configurationPayload(wire);
            case "DEVICE_COMMAND_OBSERVED" ->
                    commandObservedPayload(wire);
            case "DELIVERY_COMPLETE" ->
                    deliveryCompletePayload(
                            wire,
                            trustedCosBaseUrl);
            case "CLEAN_COMPLETE" ->
                    cleanCompletePayload(
                            wire,
                            trustedCosBaseUrl);
            case "FULLNESS_SAMPLE_COMPLETE" ->
                    fullnessSampleCompletePayload(wire);
            case "BASELINE_MEASUREMENT_COMPLETE" ->
                    baselineMeasurementCompletePayload(wire);
            case "FULLNESS_STATE_CHANGED" ->
                    fullnessStateChangedPayload(wire);
            case "PHOTO_STATUS_REPORTED" ->
                    photoStatusPayload(
                            wire,
                            trustedCosBaseUrl);
            case "PHOTO_UPLOAD_GRANT_REQUESTED" ->
                    photoGrantRequestPayload(wire);
            case "DEVICE_RUNTIME_SNAPSHOT" ->
                    runtimePayload(wire);
            case "DEVICE_SOFTWARE_STATE_REPORTED" ->
                    deviceSoftwareStatePayload(wire);
            case "DEVICE_ACCEPTANCE_EVIDENCE" ->
                    acceptanceEvidencePayload(wire);
            case "DEVICE_FAULT_OBSERVED",
                 "DEVICE_FAULT_RECOVERED" ->
                    faultPayload(wire);
            case "SAFETY_SENSOR_STATE_CHANGED" ->
                    safetyPayload(wire);
            case "BUSINESS_CONFIRMATION_RECEIPT" ->
                    confirmationReceiptPayload(wire);
            case "REMOTE_SUPPORT_TUNNEL_STATUS" ->
                    remoteSupportStatusPayload(wire);
            case "MCU_FIRMWARE_UPDATE_PROGRESS" ->
                    mcuFirmwareProgressPayload(wire);
            case "BUSINESS_RUNTIME_UPDATE_PROGRESS" ->
                    businessRuntimeProgressPayload(wire);
            case "BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT" ->
                    businessRuntimeCancellationResultPayload(wire);
            case "FACTORY_SEAL_COMPLETED" ->
                    factorySealCompletedPayload(wire);
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

    private static Map<String, Object> commandObservedPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "observedCommandType",
                enumText(
                        integer(wire, "observedCommandType"),
                        Map.of(
                                1L, "START_DELIVERY_SESSION",
                                2L, "START_CLEAN_OPERATION",
                                3L, "END_CLEAN_BEFORE_UNLOCK",
                                4L, "RESUME_CLEAN_OPERATION",
                                5L, "SAMPLE_FULLNESS",
                                6L, "MEASURE_EMPTY_BAG_BASELINE",
                                7L, "AUTHORIZE_FACTORY_SEAL"),
                        "observedCommandType"));
        String stage = enumText(
                integer(wire, "stage"),
                Map.of(
                        1L, "RECEIVED",
                        2L, "ACCEPTED",
                        3L, "REJECTED",
                        4L, "MCU_ACCEPTED",
                        5L, "PRE_START_FAILED",
                        6L, "FAILED"),
                "stage");
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
            case "RECEIVED", "ACCEPTED" ->
                    mcuCommandUid == null && errorCode == null;
            case "REJECTED" ->
                    mcuCommandUid == null && errorCode != null;
            case "MCU_ACCEPTED" ->
                    mcuCommandUid != null && errorCode == null;
            case "PRE_START_FAILED", "FAILED" ->
                    errorCode != null;
            default -> false;
        };
        if (!valid) {
            throw permanent(
                    "device command stage presence flags differ");
        }
        payload.put("stage", stage);
        payload.put("mcuCommandUid", mcuCommandUid);
        payload.put("errorCode", errorCode);
        return payload;
    }

    private static Map<String, Object> photoStatusPayload(
            JsonNode wire,
            String trustedCosBaseUrl) {
        Map<String, Object> payload = new LinkedHashMap<>();
        String workType = enumText(
                integer(wire, "workType"),
                Map.of(
                        1L, "DELIVERY_SESSION",
                        2L, "CLEAN_OPERATION"),
                "workType");
        String workUid = pattern(wire, "workUid", UUID_V4);
        payload.put("workType", workType);
        payload.put("workUid", workUid);
        payload.put(
                "photo",
                terminalPhoto(
                        object(wire, "photo"),
                        workType,
                        workUid,
                        trustedCosBaseUrl));
        return payload;
    }

    private static Map<String, Object> photoGrantRequestPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        String workType = enumText(
                integer(wire, "workType"),
                Map.of(
                        1L, "DELIVERY_SESSION",
                        2L, "CLEAN_OPERATION"),
                "workType");
        payload.put("workType", workType);
        payload.put(
                "workUid",
                pattern(wire, "workUid", UUID_V4));
        JsonNode requested = wire.get("requestedSlots");
        if (requested == null
                || !requested.isArray()
                || requested.isEmpty()
                || requested.size() > 4) {
            throw permanent(
                    "requestedSlots must contain one to four slots");
        }
        Map<Long, String> slots =
                "DELIVERY_SESSION".equals(workType)
                        ? Map.of(
                                1L, "BEFORE_INNER",
                                2L, "BEFORE_OUTER",
                                3L, "AFTER_INNER",
                                4L, "AFTER_OUTER")
                        : Map.of(
                                5L, "FIRST_OPEN_INNER",
                                6L, "FIRST_OPEN_OUTER",
                                7L, "FINAL_CLOSE_INNER",
                                8L, "FINAL_CLOSE_OUTER");
        List<String> normalized = new ArrayList<>();
        Set<String> unique = new HashSet<>();
        long previous = 0;
        for (JsonNode slot : requested) {
            if (!slot.isIntegralNumber()) {
                throw permanent(
                        "requestedSlots must contain enums");
            }
            long wireSlot = slot.longValue();
            String normalizedSlot = enumText(
                    wireSlot, slots, "requestedSlots");
            if (wireSlot <= previous
                    || !unique.add(normalizedSlot)) {
                throw permanent(
                        "requestedSlots must be unique and ordered");
            }
            previous = wireSlot;
            normalized.add(normalizedSlot);
        }
        payload.put("requestedSlots", normalized);
        payload.put(
                "reason",
                enumText(
                        integer(wire, "reason"),
                        Map.of(
                                1L, "INITIAL_GRANT_MISSING",
                                2L, "GRANT_EXPIRED",
                                3L, "EDGE_RESTARTED",
                                4L, "UPLOAD_RETRY"),
                        "reason"));
        return payload;
    }

    private static Map<String, Object> deliveryCompletePayload(
            JsonNode wire,
            String trustedCosBaseUrl) {
        Map<String, Object> payload = new LinkedHashMap<>();
        String sessionUid =
                pattern(wire, "sessionUid", UUID_V4);
        payload.put("sessionUid", sessionUid);
        payload.put(
                "portNo",
                requiredIntegerInRange(wire, "portNo", 1, 6));
        payload.put(
                "firstPreOpenMeasurement",
                nullableMeasurement(
                        wire,
                        "firstPreOpenMeasurementPresent",
                        "firstPreOpenMeasurement"));
        payload.put(
                "finalPostCloseMeasurement",
                nullableMeasurement(
                        wire,
                        "finalPostCloseMeasurementPresent",
                        "finalPostCloseMeasurement"));
        payload.put(
                "deliveryNetWeightGrams",
                nullablePresenceSignedInteger(
                        wire,
                        "deliveryNetWeightGramsPresent",
                        "deliveryNetWeightGrams"));
        payload.put(
                "finalDoorCommand",
                nullableDeliveryDoorCommand(wire));
        String completionReason = enumText(
                integer(wire, "completionReason"),
                Map.of(
                        1L, "USER_ENDED",
                        2L, "SELECTION_WINDOW_EXPIRED",
                        3L, "TERMINAL_WEIGHT_FAILURE",
                        4L, "DEVICE_INTERRUPTED"),
                "completionReason");
        payload.put("completionReason", completionReason);
        boolean manualReviewRequired =
                bool(wire, "manualReviewRequired");
        if (manualReviewRequired
                != "DEVICE_INTERRUPTED".equals(completionReason)) {
            throw permanent(
                    "delivery completion reason and manual review differ");
        }
        payload.put(
                "manualReviewRequired",
                manualReviewRequired);
        payload.put(
                "negativeWeightAnomaly",
                bool(wire, "negativeWeightAnomaly"));
        payload.put(
                "frozenConfig",
                configSnapshot(object(wire, "frozenConfig")));
        payload.put(
                "unitPriceTenThousandths",
                requiredIntegerInRange(
                        wire,
                        "unitPriceTenThousandths",
                        1,
                        4_294_967_295L));
        payload.put(
                "photos",
                deliveryPhotos(
                        wire,
                        sessionUid,
                        trustedCosBaseUrl));
        return payload;
    }

    private static Map<String, Object> configSnapshot(JsonNode wire) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put(
                "version",
                requiredIntegerInRange(
                        wire,
                        "version",
                        1,
                        SAFE_INTEGER_MAX));
        config.put(
                "contentSha256",
                pattern(wire, "contentSha256", SHA256));
        config.put(
                "mcuPayloadSha256",
                pattern(wire, "mcuPayloadSha256", SHA256));
        return config;
    }

    private static Map<String, Object> cleanCompletePayload(
            JsonNode wire,
            String trustedCosBaseUrl) {
        Map<String, Object> payload = new LinkedHashMap<>();
        String operationUid = pattern(
                wire, "operationUid", UUID_V4);
        payload.put("operationUid", operationUid);
        payload.put(
                "portNo",
                requiredIntegerInRange(wire, "portNo", 1, 6));
        payload.put(
                "oldBagUid",
                nullablePresenceText(
                        wire,
                        "oldBagUidPresent",
                        "oldBagUid",
                        UUID_V4,
                        36));
        payload.put(
                "newBagUid",
                pattern(wire, "newBagUid", UUID_V4));
        Map<String, Object> pre = measurement(
                object(wire, "preUnlockMeasurement"));
        if (!"STABLE".equals(pre.get("status"))
                || !Boolean.TRUE.equals(
                pre.get("weightValueAvailable"))
                || !"STABLE_WINDOW_MEAN".equals(
                pre.get("weightValueKind"))) {
            throw permanent(
                    "clean pre-unlock measurement must be stable");
        }
        payload.put("preUnlockMeasurement", pre);
        Map<String, Object> finalMeasurement = measurement(
                object(
                        wire,
                        "cleanerConfirmedFinalMeasurement"));
        payload.put(
                "cleanerConfirmedFinalMeasurement",
                finalMeasurement);
        payload.put(
                "removedNetWeightGrams",
                nullablePresenceSignedInteger(
                        wire,
                        "removedNetWeightGramsPresent",
                        "removedNetWeightGrams"));
        Long newBaseline = nullablePresenceSignedInteger(
                wire,
                "newBaselineWeightGramsPresent",
                "newBaselineWeightGrams");
        boolean stableFinal = "STABLE".equals(
                finalMeasurement.get("status"));
        if (stableFinal
                != (newBaseline != null)
                || (stableFinal
                && !newBaseline.equals(
                finalMeasurement.get("reportedWeightGrams")))) {
            throw permanent(
                    "clean final measurement and new baseline differ");
        }
        payload.put("newBaselineWeightGrams", newBaseline);
        if (!bool(wire, "cleanerCompletionConfirmed")) {
            throw permanent(
                    "clean completion lacks cleaner confirmation");
        }
        payload.put("cleanerCompletionConfirmed", true);
        payload.put(
                "cleanActionSequence",
                requiredIntegerInRange(
                        wire,
                        "cleanActionSequence",
                        1,
                        65_535));
        JsonNode lockWire = object(
                wire,
                "cleanLockAndManualDoorConfirmati");
        String solenoidHealth = enumText(
                integer(lockWire, "solenoidHealth"),
                Map.of(
                        1L, "OK",
                        2L, "DRIVER_FAULT",
                        3L, "DISCONNECTED",
                        4L, "UNKNOWN"),
                "solenoidHealth");
        if (integer(lockWire, "lockPowerState") != 1
                || integer(
                lockWire, "physicalDoorStateBasis") != 1
                || !bool(
                lockWire,
                "cleanerPhysicalCloseConfirmed")
                || !("OK".equals(solenoidHealth)
                || "UNKNOWN".equals(solenoidHealth))) {
            throw permanent(
                    "clean lock and manual close confirmation is unsafe");
        }
        Map<String, Object> lock = new LinkedHashMap<>();
        lock.put("lockPowerState", "DEENERGIZED");
        lock.put("solenoidHealth", solenoidHealth);
        lock.put(
                "physicalDoorStateBasis",
                "CLEANER_CONFIRMATION");
        lock.put("cleanerPhysicalCloseConfirmed", true);
        payload.put(
                "cleanLockAndManualDoorConfirmation",
                lock);
        payload.put(
                "frozenConfig",
                configSnapshot(object(wire, "frozenConfig")));
        payload.put(
                "photos",
                cleanPhotos(
                        wire,
                        operationUid,
                        trustedCosBaseUrl));
        return payload;
    }

    private static Map<String, Object> fullnessSampleCompletePayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "detectionUid",
                pattern(wire, "detectionUid", UUID_V4));
        payload.put(
                "portNo",
                requiredIntegerInRange(wire, "portNo", 1, 6));
        String sampleRole = enumText(
                integer(wire, "sampleRole"),
                Map.of(
                        1L, "INITIAL",
                        2L, "CONFIRMATION",
                        3L, "MANUAL_RECHECK"),
                "sampleRole");
        String triggerType = enumText(
                integer(wire, "triggerType"),
                Map.of(
                        1L, "DELIVERY_COMPLETE",
                        2L, "CLEAN_COMPLETE",
                        3L, "MANUAL_RECHECK"),
                "triggerType");
        if (("MANUAL_RECHECK".equals(triggerType))
                != "MANUAL_RECHECK".equals(sampleRole)) {
            throw permanent(
                    "fullness role and trigger type differ");
        }
        payload.put("sampleRole", sampleRole);
        payload.put("triggerType", triggerType);
        payload.put(
                "fullnessMode",
                enumText(
                        integer(wire, "fullnessMode"),
                        Map.of(
                                1L, "SENSOR_ONLY",
                                2L, "WEIGHT_ONLY",
                                3L, "SENSOR_OR_WEIGHT"),
                        "fullnessMode"));
        String sensorKind = enumText(
                integer(wire, "fullnessSensorKind"),
                Map.of(
                        1L, "ULTRASONIC",
                        2L, "DIGITAL_INFRARED"),
                "fullnessSensorKind");
        String sensorValue = enumText(
                integer(wire, "fullnessSensorValue"),
                Map.of(
                        1L, "CLEAR",
                        2L, "BLOCKED"),
                "fullnessSensorValue");
        String sampleBasis = enumText(
                integer(wire, "fullnessSampleBasis"),
                Map.of(
                        1L, "MEASURED_MEDIAN",
                        2L, "NO_ECHO_CLEAR_FALLBACK",
                        3L,
                        "INSUFFICIENT_VALID_SAMPLES_CLEAR_FALLBACK",
                        4L, "NOT_SAMPLED"),
                "fullnessSampleBasis");
        Long distance = nullablePresenceInteger(
                wire,
                "representativeDistanceMmPresent",
                "representativeDistanceMm",
                false);
        int requested = Math.toIntExact(
                requiredIntegerInRange(
                        wire,
                        "requestedSampleCount",
                        0,
                        255));
        int valid = Math.toIntExact(
                requiredIntegerInRange(
                        wire,
                        "validSampleCount",
                        0,
                        255));
        if (valid > requested) {
            throw permanent(
                    "fullness valid samples exceed requested samples");
        }
        if (!"DIGITAL_INFRARED".equals(sensorKind)
                || !"NOT_SAMPLED".equals(sampleBasis)
                || distance != null
                || !((requested == 1 && valid == 1)
                || (requested == 0 && valid == 0))) {
            throw permanent(
                    "fullness result is outside the accepted fixed-frame shape");
        }
        FullnessCompatibilityMeasurement compatibility =
                requireFixedFrameFullnessMeasurement(
                        measurement(object(
                                wire,
                                "totalWeightMeasurement")),
                        requested);
        payload.put("fullnessSensorKind", sensorKind);
        payload.put("fullnessSensorValue", sensorValue);
        payload.put("fullnessSampleBasis", sampleBasis);
        payload.put("representativeDistanceMm", distance);
        payload.put("requestedSampleCount", requested);
        payload.put("validSampleCount", valid);
        payload.put(
                "totalWeightMeasurement",
                compatibility.measurement());
        payload.put(
                "frozenConfig",
                configSnapshot(object(wire, "frozenConfig")));
        return payload;
    }

    private static Map<String, Object> fullnessStateChangedPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "stateChangeUid",
                pattern(wire, "stateChangeUid", UUID_V4));
        payload.put(
                "portNo",
                requiredIntegerInRange(wire, "portNo", 1, 6));
        payload.put("bagUid", pattern(wire, "bagUid", UUID_V4));
        payload.put(
                "state",
                enumText(
                        integer(wire, "state"),
                        Map.of(1L, "FULL", 2L, "NOT_FULL"),
                        "state"));
        payload.put(
                "sourceWorkType",
                enumText(
                        integer(wire, "sourceWorkType"),
                        Map.of(
                                1L, "DELIVERY_SESSION",
                                2L, "CLEAN_OPERATION"),
                        "sourceWorkType"));
        payload.put(
                "sourceWorkUid",
                pattern(wire, "sourceWorkUid", UUID_V4));
        payload.put(
                "fullnessMode",
                enumText(
                        integer(wire, "fullnessMode"),
                        Map.of(
                                1L, "SENSOR_ONLY",
                                2L, "WEIGHT_ONLY",
                                3L, "SENSOR_OR_WEIGHT"),
                        "fullnessMode"));
        payload.put(
                "fullnessSensorKind",
                enumText(
                        integer(wire, "fullnessSensorKind"),
                        Map.of(
                                1L, "ULTRASONIC",
                                2L, "DIGITAL_INFRARED"),
                        "fullnessSensorKind"));
        payload.put(
                "fullnessSensorValue",
                enumText(
                        integer(wire, "fullnessSensorValue"),
                        Map.of(
                                1L, "CLEAR",
                                2L, "BLOCKED",
                                3L, "NOT_SAMPLED"),
                        "fullnessSensorValue"));
        payload.put(
                "confirmationBasis",
                enumText(
                        integer(wire, "confirmationBasis"),
                        Map.of(
                                1L,
                                "FIXED_FRAME_CACHED_FINAL_OBSERVATION",
                                2L, "MCU_INDEPENDENT_RECHECK"),
                        "confirmationBasis"));
        payload.put(
                "totalWeightMeasurement",
                measurement(object(wire, "totalWeightMeasurement")));
        payload.put(
                "baselineWeightGrams",
                nullablePresenceSignedInteger(
                        wire,
                        "baselineWeightGramsPresent",
                        "baselineWeightGrams"));
        payload.put(
                "configuredFullWeightGrams",
                requiredIntegerInRange(
                        wire,
                        "configuredFullWeightGrams",
                        1,
                        4_294_967_295L));
        payload.put(
                "fullnessPercentHundredths",
                nullablePresenceIntegerInRange(
                        wire,
                        "fullnessPercentHundredthsPresent",
                        "fullnessPercentHundredths",
                        0,
                        SAFE_INTEGER_MAX));
        payload.put(
                "weightFull",
                nullablePresenceBoolean(
                        wire,
                        "weightFullPresent",
                        "weightFull"));
        payload.put(
                "frozenConfig",
                configSnapshot(object(wire, "frozenConfig")));
        return payload;
    }

    private static Map<String, Object> baselineMeasurementCompletePayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "measurementUid",
                pattern(wire, "measurementUid", UUID_V4));
        payload.put(
                "portNo",
                requiredIntegerInRange(wire, "portNo", 1, 6));
        payload.put(
                "bagUid",
                pattern(wire, "bagUid", UUID_V4));
        if (!bool(wire, "emptyBagConfirmed")) {
            throw permanent(
                    "baseline measurement does not confirm an empty bag");
        }
        payload.put("emptyBagConfirmed", true);
        payload.put(
                "totalWeightMeasurement",
                measurement(object(wire, "totalWeightMeasurement")));
        payload.put(
                "frozenConfig",
                configSnapshot(object(wire, "frozenConfig")));
        return payload;
    }

    private static FullnessCompatibilityMeasurement
            requireFixedFrameFullnessMeasurement(
                    Map<String, Object> measurement,
                    int requestedSampleCount) {
        Object weight = measurement.get("reportedWeightGrams");
        if (!"STABLE".equals(measurement.get("status"))
                || !Boolean.TRUE.equals(
                measurement.get("weightValueAvailable"))
                || !(weight instanceof Long)
                || !"STABLE_WINDOW_MEAN".equals(
                measurement.get("weightValueKind"))
                || !Long.valueOf(1).equals(
                measurement.get("sampleCount"))
                || !"OK".equals(measurement.get("sensorHealth"))
                || measurement.get("faultCode") != null
                || (requestedSampleCount == 0
                && !Long.valueOf(0).equals(weight))) {
            throw permanent(
                    "fullness fixed-frame weight quality fields differ");
        }
        return new FullnessCompatibilityMeasurement(measurement);
    }

    private static Map<String, Object> nullableMeasurement(
            JsonNode parent,
            String presenceField,
            String valueField) {
        boolean present = bool(parent, presenceField);
        JsonNode value = parent.get(valueField);
        if (!present) {
            if (value != null
                    && !value.isNull()
                    && !value.isObject()) {
                throw permanent(
                        valueField
                                + " placeholder must be an object");
            }
            return null;
        }
        if (value == null || !value.isObject()) {
            throw permanent(
                    valueField
                            + " must exist when marked present");
        }
        return measurement(value);
    }

    private static Map<String, Object> measurement(JsonNode wire) {
        Map<String, Object> measurement = new LinkedHashMap<>();
        measurement.put(
                "measurementUid",
                pattern(wire, "measurementUid", UUID_V4));
        String status = enumText(
                integer(wire, "status"),
                MEASUREMENT_STATUS,
                "status");
        measurement.put("status", status);
        boolean valueAvailable =
                bool(wire, "weightValueAvailable");
        measurement.put(
                "weightValueAvailable",
                valueAvailable);
        Long reportedWeight = nullablePresenceSignedInteger(
                wire,
                "reportedWeightGramsPresent",
                "reportedWeightGrams");
        if (reportedWeight != null
                && (reportedWeight < Integer.MIN_VALUE
                || reportedWeight > Integer.MAX_VALUE)) {
            throw permanent(
                    "reportedWeightGrams is outside int32");
        }
        measurement.put(
                "reportedWeightGrams",
                reportedWeight);
        String valueKind = enumText(
                integer(wire, "weightValueKind"),
                WEIGHT_VALUE_KIND,
                "weightValueKind");
        measurement.put("weightValueKind", valueKind);
        measurement.put(
                "measurementElapsedMs",
                requiredIntegerInRange(
                        wire,
                        "measurementElapsedMs",
                        0,
                        4_294_967_295L));
        long sampleCount = requiredIntegerInRange(
                wire,
                "sampleCount",
                0,
                65_535);
        measurement.put("sampleCount", sampleCount);
        measurement.put(
                "calibrationVersion",
                requiredIntegerInRange(
                        wire,
                        "calibrationVersion",
                        0,
                        4_294_967_295L));
        String sensorHealth = enumText(
                integer(wire, "sensorHealth"),
                SENSOR_HEALTH,
                "sensorHealth");
        measurement.put("sensorHealth", sensorHealth);
        String faultCode = nullablePresenceEnum(
                wire,
                "faultCodePresent",
                "faultCode",
                FAULT_CODE);
        if (faultCode != null
                && !MEASUREMENT_FAULT_CODES.contains(faultCode)) {
            throw permanent(
                    "measurement faultCode is outside its contract");
        }
        measurement.put("faultCode", faultCode);
        measurement.put(
                "mcuBootId",
                requiredIntegerInRange(
                        wire,
                        "mcuBootId",
                        1,
                        SAFE_INTEGER_MAX));
        measurement.put(
                "mcuEventSequence",
                requiredIntegerInRange(
                        wire,
                        "mcuEventSequence",
                        1,
                        4_294_967_295L));
        validateMeasurementShape(
                status,
                valueAvailable,
                reportedWeight,
                valueKind,
                sampleCount,
                sensorHealth,
                faultCode);
        return measurement;
    }

    private static void validateMeasurementShape(
            String status,
            boolean valueAvailable,
            Long reportedWeight,
            String valueKind,
            long sampleCount,
            String sensorHealth,
            String faultCode) {
        if (valueAvailable != (reportedWeight != null)
                || (valueAvailable && "NONE".equals(valueKind))
                || (!valueAvailable && !"NONE".equals(valueKind))) {
            throw permanent(
                    "measurement value availability fields differ");
        }
        if ("STABLE".equals(status)
                && (!valueAvailable
                || !"STABLE_WINDOW_MEAN".equals(valueKind)
                || sampleCount < 1
                || !"OK".equals(sensorHealth)
                || faultCode != null)) {
            throw permanent(
                    "stable measurement quality fields differ");
        }
        if ("UNSTABLE".equals(status)
                && (!valueAvailable
                || (!"LAST_FOUR_MEAN".equals(valueKind)
                && !"AVAILABLE_SAMPLES_MEAN".equals(valueKind))
                || !"OK".equals(sensorHealth)
                || !"WEIGHT_UNSTABLE".equals(faultCode))) {
            throw permanent(
                    "unstable measurement quality fields differ");
        }
    }

    private static Map<String, Object> nullableDeliveryDoorCommand(
            JsonNode wire) {
        boolean present = bool(wire, "finalDoorCommandPresent");
        JsonNode value = wire.get("finalDoorCommand");
        if (!present) {
            if (value != null
                    && !value.isNull()
                    && !value.isObject()) {
                throw permanent(
                        "finalDoorCommand placeholder must be an object");
            }
            return null;
        }
        if (value == null || !value.isObject()) {
            throw permanent(
                    "finalDoorCommand must exist when marked present");
        }
        Map<String, Object> command = new LinkedHashMap<>();
        command.put(
                "command",
                enumText(
                        integer(value, "command"),
                        Map.of(
                                1L, "NONE",
                                2L, "OPEN",
                                3L, "CLOSE"),
                        "finalDoorCommand.command"));
        command.put(
                "outputStatus",
                enumText(
                        integer(value, "outputStatus"),
                        Map.of(
                                1L, "NOT_DISPATCHED",
                                2L, "COMMAND_DISPATCHED",
                                3L,
                                "COMMAND_SUPERSEDED_BEFORE_DISPATCH",
                                4L, "COALESCED_WITH_EXISTING_CLOSE",
                                5L, "OUTPUT_REJECTED"),
                        "finalDoorCommand.outputStatus"));
        command.put(
                "physicalStateBasis",
                exactEnum(
                        value,
                        "physicalStateBasis",
                        1,
                        "NOT_OBSERVABLE"));
        return command;
    }

    private static List<Map<String, Object>> deliveryPhotos(
            JsonNode wire,
            String sessionUid,
            String trustedCosBaseUrl) {
        JsonNode photos = wire.get("photos");
        if (photos == null
                || !photos.isArray()
                || photos.size() != 4) {
            throw permanent(
                    "delivery photos must contain exactly four slots");
        }
        List<Map<String, Object>> normalized = new ArrayList<>();
        Set<String> seenSlots = new HashSet<>();
        for (JsonNode photo : photos) {
            if (!photo.isObject()) {
                throw permanent(
                        "delivery photo slot must be an object");
            }
            Map<String, Object> value = deliveryPhoto(
                    photo,
                    sessionUid,
                    trustedCosBaseUrl);
            String slot = (String) value.get("slot");
            if (!seenSlots.add(slot)) {
                throw permanent(
                        "delivery photo slots must be unique");
            }
            normalized.add(value);
        }
        if (!seenSlots.equals(DELIVERY_PHOTO_SLOTS)) {
            throw permanent(
                    "delivery photo slots differ from the required set");
        }
        return normalized;
    }

    private static List<Map<String, Object>> cleanPhotos(
            JsonNode wire,
            String operationUid,
            String trustedCosBaseUrl) {
        JsonNode photos = wire.get("photos");
        if (photos == null
                || !photos.isArray()
                || photos.size() != 4) {
            throw permanent(
                    "clean photos must contain exactly four slots");
        }
        List<Map<String, Object>> normalized = new ArrayList<>();
        Set<String> seenSlots = new HashSet<>();
        for (JsonNode photo : photos) {
            if (!photo.isObject()) {
                throw permanent(
                        "clean photo slot must be an object");
            }
            Map<String, Object> value = cleanPhoto(
                    photo,
                    operationUid,
                    trustedCosBaseUrl);
            String slot = (String) value.get("slot");
            if (!seenSlots.add(slot)) {
                throw permanent(
                        "clean photo slots must be unique");
            }
            normalized.add(value);
        }
        if (!seenSlots.equals(CLEAN_PHOTO_SLOTS)) {
            throw permanent(
                    "clean photo slots differ from the required set");
        }
        return normalized;
    }

    private static Map<String, Object> cleanPhoto(
            JsonNode wire,
            String operationUid,
            String trustedCosBaseUrl) {
        Map<String, Object> photo = new LinkedHashMap<>();
        String slot = text(wire, "slot", 32);
        if (!CLEAN_PHOTO_SLOTS.contains(slot)) {
            throw permanent("clean photo slot is unsupported");
        }
        photo.put("slot", slot);
        String status = enumText(
                integer(wire, "status"),
                Map.of(
                        1L, "AVAILABLE",
                        2L, "UPLOAD_PENDING",
                        3L, "PERMANENTLY_MISSING"),
                "photo.status");
        photo.put("status", status);
        String photoUid = nullablePresenceText(
                wire,
                "photoUidPresent",
                "photoUid",
                UUID_V4,
                36);
        photo.put("photoUid", photoUid);
        String url = nullablePresenceText(
                wire,
                "urlPresent",
                "url",
                "^https://[^?#]+$",
                512);
        photo.put("url", url);
        String sha256 = nullablePresenceText(
                wire,
                "sha256Present",
                "sha256",
                SHA256,
                64);
        photo.put("sha256", sha256);
        Long sizeBytes = nullablePresenceIntegerInRange(
                wire,
                "sizeBytesPresent",
                "sizeBytes",
                1,
                20_971_520);
        photo.put("sizeBytes", sizeBytes);
        String capturedAt = nullablePresenceInstant(
                wire,
                "capturedAtPresent",
                "capturedAt");
        photo.put("capturedAt", capturedAt);
        String missingReason = nullablePresenceText(
                wire,
                "missingReasonPresent",
                "missingReason",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        photo.put("missingReason", missingReason);
        validatePhotoShape(
                status,
                photoUid,
                url,
                sha256,
                sizeBytes,
                capturedAt,
                missingReason);
        if (url != null) {
            String baseUrl = trustedCosBaseUrl == null
                    ? ""
                    : trustedCosBaseUrl.replaceFirst("/+$", "");
            String expectedUrl = baseUrl
                    + "/ecobin/"
                    + "clean-operation/"
                    + operationUid
                    + "/"
                    + slot
                    + "/"
                    + photoUid
                    + ".jpg";
            if (baseUrl.isEmpty() || !expectedUrl.equals(url)) {
                throw permanent(
                        "available clean photo URL is outside the trusted COS work path");
            }
        }
        return photo;
    }

    private static Map<String, Object> deliveryPhoto(
            JsonNode wire,
            String sessionUid,
            String trustedCosBaseUrl) {
        Map<String, Object> photo = new LinkedHashMap<>();
        String slot = text(wire, "slot", 32);
        if (!DELIVERY_PHOTO_SLOTS.contains(slot)) {
            throw permanent(
                    "delivery photo slot is unsupported");
        }
        photo.put("slot", slot);
        String status = enumText(
                integer(wire, "status"),
                Map.of(
                        1L, "AVAILABLE",
                        2L, "UPLOAD_PENDING",
                        3L, "PERMANENTLY_MISSING"),
                "photo.status");
        photo.put("status", status);
        String photoUid = nullablePresenceText(
                wire,
                "photoUidPresent",
                "photoUid",
                UUID_V4,
                36);
        photo.put("photoUid", photoUid);
        String url = nullablePresenceText(
                wire,
                "urlPresent",
                "url",
                "^https://[^?#]+$",
                512);
        photo.put("url", url);
        String sha256 = nullablePresenceText(
                wire,
                "sha256Present",
                "sha256",
                SHA256,
                64);
        photo.put("sha256", sha256);
        Long sizeBytes = nullablePresenceIntegerInRange(
                wire,
                "sizeBytesPresent",
                "sizeBytes",
                1,
                20_971_520);
        photo.put("sizeBytes", sizeBytes);
        String capturedAt = nullablePresenceInstant(
                wire,
                "capturedAtPresent",
                "capturedAt");
        photo.put("capturedAt", capturedAt);
        String missingReason = nullablePresenceText(
                wire,
                "missingReasonPresent",
                "missingReason",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        photo.put("missingReason", missingReason);
        validatePhotoShape(
                status,
                photoUid,
                url,
                sha256,
                sizeBytes,
                capturedAt,
                missingReason);
        if (url != null) {
            String baseUrl = trustedCosBaseUrl == null
                    ? ""
                    : trustedCosBaseUrl.replaceFirst("/+$", "");
            String expectedUrl = baseUrl
                    + "/ecobin/"
                    + "delivery-session/"
                    + sessionUid
                    + "/"
                    + slot
                    + "/"
                    + photoUid
                    + ".jpg";
            if (baseUrl.isEmpty()
                    || !expectedUrl.equals(url)) {
                throw permanent(
                        "available photo URL is outside the trusted COS work path");
            }
        }
        return photo;
    }

    private static Map<String, Object> terminalPhoto(
            JsonNode wire,
            String workType,
            String workUid,
            String trustedCosBaseUrl) {
        Map<Long, String> slotMapping;
        Set<String> allowedSlots;
        String workPath;
        if ("DELIVERY_SESSION".equals(workType)) {
            slotMapping = Map.of(
                    1L, "BEFORE_INNER",
                    2L, "BEFORE_OUTER",
                    3L, "AFTER_INNER",
                    4L, "AFTER_OUTER");
            allowedSlots = DELIVERY_PHOTO_SLOTS;
            workPath = "delivery-session";
        } else if ("CLEAN_OPERATION".equals(workType)) {
            slotMapping = Map.of(
                    5L, "FIRST_OPEN_INNER",
                    6L, "FIRST_OPEN_OUTER",
                    7L, "FINAL_CLOSE_INNER",
                    8L, "FINAL_CLOSE_OUTER");
            allowedSlots = CLEAN_PHOTO_SLOTS;
            workPath = "clean-operation";
        } else {
            throw permanent("photo work type is unsupported");
        }

        Map<String, Object> photo = new LinkedHashMap<>();
        String slot = enumText(
                integer(wire, "slot"),
                slotMapping,
                "photo.slot");
        if (!allowedSlots.contains(slot)) {
            throw permanent(
                    "photo slot is outside its work type");
        }
        photo.put("slot", slot);
        String status = enumText(
                integer(wire, "status"),
                Map.of(
                        1L, "AVAILABLE",
                        2L, "PERMANENTLY_MISSING"),
                "photo.status");
        photo.put("status", status);
        String photoUid = nullablePresenceText(
                wire,
                "photoUidPresent",
                "photoUid",
                UUID_V4,
                36);
        photo.put("photoUid", photoUid);
        String url = nullablePresenceText(
                wire,
                "urlPresent",
                "url",
                "^https://[^?#]+$",
                512);
        photo.put("url", url);
        String sha256 = nullablePresenceText(
                wire,
                "sha256Present",
                "sha256",
                SHA256,
                64);
        photo.put("sha256", sha256);
        Long sizeBytes = nullablePresenceIntegerInRange(
                wire,
                "sizeBytesPresent",
                "sizeBytes",
                1,
                20_971_520);
        photo.put("sizeBytes", sizeBytes);
        String capturedAt = nullablePresenceInstant(
                wire,
                "capturedAtPresent",
                "capturedAt");
        photo.put("capturedAt", capturedAt);
        String missingReason = nullablePresenceText(
                wire,
                "missingReasonPresent",
                "missingReason",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        photo.put("missingReason", missingReason);
        validatePhotoShape(
                status,
                photoUid,
                url,
                sha256,
                sizeBytes,
                capturedAt,
                missingReason);
        if ("AVAILABLE".equals(status)) {
            String baseUrl = trustedCosBaseUrl == null
                    ? ""
                    : trustedCosBaseUrl.replaceFirst("/+$", "");
            String expectedUrl = baseUrl
                    + "/ecobin/"
                    + workPath
                    + "/"
                    + workUid
                    + "/"
                    + slot
                    + "/"
                    + photoUid
                    + ".jpg";
            if (baseUrl.isEmpty()
                    || !expectedUrl.equals(url)) {
                throw permanent(
                        "available photo URL is outside the trusted COS work path");
            }
        }
        return photo;
    }

    private static void validatePhotoShape(
            String status,
            String photoUid,
            String url,
            String sha256,
            Long sizeBytes,
            String capturedAt,
            String missingReason) {
        boolean captured = photoUid != null;
        boolean capturedMetadataComplete =
                captured && sha256 != null && sizeBytes != null;
        boolean noCapturedMetadata =
                photoUid == null
                        && sha256 == null
                        && sizeBytes == null
                        && capturedAt == null;
        boolean valid = switch (status) {
            case "AVAILABLE" ->
                    capturedMetadataComplete
                            && url != null
                            && missingReason == null;
            case "UPLOAD_PENDING" ->
                    url == null
                            && missingReason != null
                            && ((capturedMetadataComplete)
                            || noCapturedMetadata);
            case "PERMANENTLY_MISSING" ->
                    url == null
                            && missingReason != null
                            && (capturedMetadataComplete
                            || noCapturedMetadata);
            default -> false;
        };
        if (!valid
                || (!captured && capturedAt != null)) {
            throw permanent(
                    "photo status and nullable fields differ");
        }
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
        String mcuFirmwareVersion = nullablePresenceText(
                        wire,
                        "mcuFirmwareVersionPresent",
                        "mcuFirmwareVersion",
                        "^.{1,64}$",
                        64);
        payload.put("mcuFirmwareVersion", mcuFirmwareVersion);
        JsonNode identityPresence = wire.get(
                "mcuFirmwareIdentityPresent");
        boolean identityPresent = identityPresence != null
                && bool(wire, "mcuFirmwareIdentityPresent");
        if (identityPresent) {
            JsonNode identity = wire.get("mcuFirmwareIdentity");
            if (identity == null || !identity.isObject()) {
                throw permanent(
                        "mcuFirmwareIdentity must exist when marked present");
            }
            Map<String, Object> observation = new LinkedHashMap<>();
            observation.put(
                    "queryStatus",
                    exactEnum(identity, "queryStatus", 1L, "OK"));
            observation.put(
                    "statusCode",
                    exactEnum(identity, "statusCode", 1L, 0L));
            observation.put(
                    "fixedFrameRevision",
                    exactEnum(
                            identity,
                            "fixedFrameRevision",
                            1L,
                            2L));
            long versionCode = integer(
                    identity,
                    "firmwareVersionCode");
            if (versionCode < 1 || versionCode > 4_294_967_295L) {
                throw permanent(
                        "firmwareVersionCode is outside uint32");
            }
            observation.put("firmwareVersionCode", versionCode);
            String identityVersion = text(
                    identity,
                    "firmwareVersion",
                    32);
            if (identityVersion.length() < 5
                    || !identityVersion.equals(mcuFirmwareVersion)) {
                throw permanent(
                        "MCU identity and runtime firmware versions differ");
            }
            observation.put("firmwareVersion", identityVersion);
            observation.put(
                    "firmwareIdentityHex",
                    pattern(
                            identity,
                            "firmwareIdentityHex",
                            "^[0-9a-f]{16}$"));
            payload.put("mcuFirmwareIdentity", observation);
        }
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
        Long uartProtocolMajor = nullablePresenceInteger(
                wire,
                "uartProtocolMajorPresent",
                "uartProtocolMajor",
                false);
        Long uartProtocolMinor = nullablePresenceInteger(
                wire,
                "uartProtocolMinorPresent",
                "uartProtocolMinor",
                false);
        if ((uartProtocolMajor == null) != (uartProtocolMinor == null)) {
            throw permanent("UART protocol version fields differ");
        }
        payload.put("uartProtocolMajor", uartProtocolMajor);
        payload.put("uartProtocolMinor", uartProtocolMinor);
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
        JsonNode clockOffsetPresence = wire.get("clockOffsetMillisPresent");
        JsonNode clockOffsetValue = wire.get("clockOffsetMillis");
        boolean clockOffsetPresent = clockOffsetPresence == null
                ? clockOffsetValue != null && !clockOffsetValue.isNull()
                : bool(wire, "clockOffsetMillisPresent");
        if (clockOffsetPresent) {
            long clockOffsetMillis = integer(wire, "clockOffsetMillis");
            if (clockOffsetMillis < -86_400_000L
                    || clockOffsetMillis > 86_400_000L) {
                throw permanent("clockOffsetMillis is outside accepted range");
            }
            payload.put("clockOffsetMillis", clockOffsetMillis);
        }
        JsonNode clockRepairPresence = wire.get("clockRepairStatePresent");
        JsonNode clockRepairValue = wire.get("clockRepairState");
        boolean clockRepairPresent = clockRepairPresence == null
                ? clockRepairValue != null && !clockRepairValue.isNull()
                : bool(wire, "clockRepairStatePresent");
        if (clockRepairPresent) {
            payload.put(
                    "clockRepairState",
                    enumText(
                            integer(wire, "clockRepairState"),
                            Map.of(
                                    1L, "NOT_ATTEMPTED",
                                    2L, "NOT_APPLICABLE",
                                    3L, "PROVIDER_UNAVAILABLE",
                                    4L, "REPAIR_REQUESTED",
                                    5L, "REPAIR_FAILED",
                                    6L, "SYNCHRONIZED"),
                            "clockRepairState"));
        }
        payload.put(
                "pendingReliableEventCount",
                nonNegativeSafeInteger(
                        wire, "pendingReliableEventCount"));
        JsonNode ports = wire.get("ports");
        if (ports == null || !ports.isArray() || ports.isEmpty()) {
            throw permanent("runtime ports must be a non-empty array");
        }
        List<Map<String, Object>> normalizedPorts = new ArrayList<>();
        boolean fixedFrameCompatibility =
                uartProtocolMajor == null && uartProtocolMinor == null;
        for (JsonNode port : ports) {
            normalizedPorts.add(runtimePort(
                    port, fixedFrameCompatibility));
        }
        payload.put("ports", normalizedPorts);
        return payload;
    }

    private static Map<String, Object> deviceSoftwareStatePayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "managementStateSequence",
                positiveSafeInteger(wire, "managementStateSequence"));
        payload.put(
                "managementArchitectureGeneration",
                exactEnum(
                        wire,
                        "managementArchitectureGeneration",
                        1L,
                        "PERMANENT_V1"));
        payload.put(
                "businessAdmissionState",
                enumText(
                        integer(wire, "businessAdmissionState"),
                        Map.of(
                                1L, "OPEN",
                                2L, "DRAINING",
                                3L, "MAINTENANCE",
                                4L, "LOCKED"),
                        "businessAdmissionState"));
        payload.put(
                "communicationAgent",
                deviceSoftwareCommunicationAgent(
                        object(wire, "communicationAgent")));
        payload.put(
                "deviceUpdater",
                deviceSoftwareUpdater(object(wire, "deviceUpdater")));
        payload.put(
                "activeBusinessRelease",
                activeBusinessRelease(wire));
        String businessProcessState = enumText(
                integer(wire, "businessProcessState"),
                Map.of(
                        1L, "STOPPED",
                        2L, "STARTING",
                        3L, "RUNNING",
                        4L, "FAILED"),
                "businessProcessState");
        payload.put("businessProcessState", businessProcessState);
        boolean businessReady = bool(wire, "businessReady");
        payload.put("businessReady", businessReady);
        Map<String, Object> negotiated = negotiatedProtocols(
                object(wire, "negotiatedProtocols"));
        payload.put("negotiatedProtocols", negotiated);
        payload.put("mcuFirmware", deviceSoftwareMcuFirmware(wire));
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
        payload.put("uartProtocol", deviceSoftwareUartProtocol(wire));
        payload.put(
                "capabilityBitmapHex",
                pattern(
                        wire,
                        "capabilityBitmapHex",
                        "^[0-9a-f]{16}$"));

        if (businessReady
                && (!"RUNNING".equals(businessProcessState)
                || payload.get("activeBusinessRelease") == null
                || !Boolean.TRUE.equals(
                negotiated.get("agentBusinessNegotiated"))
                || !Boolean.TRUE.equals(
                negotiated.get("updaterBusinessNegotiated")))) {
            throw permanent(
                    "ready business software lacks its active release or "
                            + "business-facing negotiated protocols");
        }
        return payload;
    }

    private static Map<String, Object> deviceSoftwareCommunicationAgent(
            JsonNode wire) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("versionName", deviceSoftwareVersion(wire, "versionName"));
        value.put(
                "managementTransportProtocolMajor",
                protocolMajor(wire, "managementTransportProtocolMajor"));
        value.put(
                "managementTransportProtocolMinor",
                protocolPart(wire, "managementTransportProtocolMinor"));
        value.put(
                "businessLocalProtocolMajor",
                protocolMajor(wire, "businessLocalProtocolMajor"));
        value.put(
                "businessLocalProtocolMinor",
                protocolPart(wire, "businessLocalProtocolMinor"));
        value.put(
                "updaterLocalProtocolMajor",
                protocolMajor(wire, "updaterLocalProtocolMajor"));
        value.put(
                "updaterLocalProtocolMinor",
                protocolPart(wire, "updaterLocalProtocolMinor"));
        return value;
    }

    private static Map<String, Object> deviceSoftwareUpdater(JsonNode wire) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("versionName", deviceSoftwareVersion(wire, "versionName"));
        value.put(
                "deviceMaintenanceProtocolMajor",
                protocolMajor(wire, "deviceMaintenanceProtocolMajor"));
        value.put(
                "deviceMaintenanceProtocolMinor",
                protocolPart(wire, "deviceMaintenanceProtocolMinor"));
        value.put(
                "businessLocalProtocolMajor",
                protocolMajor(wire, "businessLocalProtocolMajor"));
        value.put(
                "businessLocalProtocolMinor",
                protocolPart(wire, "businessLocalProtocolMinor"));
        value.put(
                "businessPackageFormatVersion",
                requiredIntegerInRange(
                        wire,
                        "businessPackageFormatVersion",
                        1L,
                        65_535L));
        value.put(
                "mcuPackageFormatVersion",
                requiredIntegerInRange(
                        wire,
                        "mcuPackageFormatVersion",
                        1L,
                        65_535L));
        return value;
    }

    private static Map<String, Object> activeBusinessRelease(JsonNode wire) {
        if (!bool(wire, "activeBusinessReleasePresent")) {
            return null;
        }
        JsonNode release = object(wire, "activeBusinessRelease");
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("releaseUid", pattern(release, "releaseUid", UUID_V4));
        value.put(
                "releaseSequence",
                positiveSafeInteger(release, "releaseSequence"));
        value.put(
                "versionName",
                deviceSoftwareVersion(release, "versionName"));
        value.put(
                "packageSha256",
                pattern(release, "packageSha256", SHA256));
        return value;
    }

    private static Map<String, Object> negotiatedProtocols(JsonNode wire) {
        Map<String, Object> value = new LinkedHashMap<>();
        negotiatedProtocolPair(value, wire, "agentBusiness");
        negotiatedProtocolPair(value, wire, "agentUpdater");
        negotiatedProtocolPair(value, wire, "updaterBusiness");
        return value;
    }

    private static void negotiatedProtocolPair(
            Map<String, Object> target,
            JsonNode wire,
            String prefix) {
        String negotiatedField = prefix + "Negotiated";
        String majorField = prefix + "Major";
        String minorField = prefix + "Minor";
        boolean negotiated = bool(wire, negotiatedField);
        long major = protocolPart(wire, majorField);
        long minor = protocolPart(wire, minorField);
        if ((negotiated && major == 0L)
                || (!negotiated && (major != 0L || minor != 0L))) {
            throw permanent(
                    prefix + " protocol negotiation fields differ");
        }
        target.put(negotiatedField, negotiated);
        target.put(majorField, major);
        target.put(minorField, minor);
    }

    private static Map<String, Object> deviceSoftwareMcuFirmware(
            JsonNode wire) {
        if (!bool(wire, "mcuFirmwarePresent")) {
            return null;
        }
        JsonNode firmware = object(wire, "mcuFirmware");
        Map<String, Object> value = new LinkedHashMap<>();
        value.put(
                "versionName",
                deviceSoftwareVersion(firmware, "versionName"));
        value.put(
                "versionCode",
                requiredIntegerInRange(
                        firmware,
                        "versionCode",
                        1L,
                        4_294_967_295L));
        value.put(
                "identityHex",
                pattern(firmware, "identityHex", "^[0-9a-f]{16}$"));
        value.put(
                "fixedFrameRevision",
                requiredIntegerInRange(
                        firmware, "fixedFrameRevision", 1L, 255L));
        return value;
    }

    private static Map<String, Object> deviceSoftwareUartProtocol(
            JsonNode wire) {
        if (!bool(wire, "uartProtocolPresent")) {
            return null;
        }
        JsonNode protocol = object(wire, "uartProtocol");
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("major", protocolMajor(protocol, "major"));
        value.put("minor", protocolPart(protocol, "minor"));
        return value;
    }

    private static String deviceSoftwareVersion(
            JsonNode wire, String field) {
        return pattern(
                wire,
                field,
                "^[0-9A-Za-z][0-9A-Za-z._+\\-]{0,31}$");
    }

    private static long protocolPart(JsonNode wire, String field) {
        return requiredIntegerInRange(wire, field, 0L, 255L);
    }

    private static long protocolMajor(JsonNode wire, String field) {
        return requiredIntegerInRange(wire, field, 1L, 255L);
    }

    private static Map<String, Object> acceptanceEvidencePayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        long wireEvidenceSchemaVersion = integer(
                wire, "evidenceSchemaVersion");
        long evidenceSchemaVersion;
        if (wireEvidenceSchemaVersion == 1L) {
            evidenceSchemaVersion = 3L;
        } else if (wireEvidenceSchemaVersion == 2L) {
            evidenceSchemaVersion = 4L;
        } else {
            throw permanent(
                    "evidenceSchemaVersion has an unsupported enum value");
        }
        payload.put("evidenceSchemaVersion", evidenceSchemaVersion);
        payload.put(
                "challengeUid",
                pattern(wire, "challengeUid", UUID_V4));
        payload.put(
                "factoryBagRevision",
                nonNegativeSafeInteger(wire, "factoryBagRevision"));
        payload.put(
                "factoryBagSetSha256",
                pattern(wire, "factoryBagSetSha256", SHA256));
        payload.put(
                "edgeSoftwareVersion",
                text(wire, "edgeSoftwareVersion", 64));
        payload.put(
                "edgeProtocolVersion",
                enumText(
                        integer(wire, "edgeProtocolVersion"),
                        EDGE_PROTOCOL_VERSION,
                        "edgeProtocolVersion"));
        payload.put(
                "edgeStoreInstanceUid",
                pattern(wire, "edgeStoreInstanceUid", UUID_V4));
        payload.put(
                "mcuFirmwareVersion",
                text(wire, "mcuFirmwareVersion", 64));
        payload.put(
                "persistentStoreHealthy",
                bool(wire, "persistentStoreHealthy"));
        payload.put(
                "trustedTimeHealthy",
                bool(wire, "trustedTimeHealthy"));
        payload.put(
                "configurationPersistenceHealthy",
                bool(wire, "configurationPersistenceHealthy"));
        payload.put(
                "mcuCommunicationHealthy",
                bool(wire, "mcuCommunicationHealthy"));
        JsonNode capabilityPresence = wire.get(
                "mcuRemoteUpdateCapablePresent");
        JsonNode capabilityValue = wire.get(
                "mcuRemoteUpdateCapable");
        if (evidenceSchemaVersion == 3L) {
            if (capabilityPresence == null) {
                if (capabilityValue != null) {
                    throw permanent(
                            "v3 remote-update capability has no presence flag");
                }
            } else if (nullablePresenceBoolean(
                    wire,
                    "mcuRemoteUpdateCapablePresent",
                    "mcuRemoteUpdateCapable") != null) {
                throw permanent(
                        "v3 acceptance evidence carries v4 capability");
            }
        } else {
            if (capabilityPresence == null) {
                throw permanent(
                        "v4 remote-update capability presence is missing");
            }
            Boolean capability = nullablePresenceBoolean(
                    wire,
                    "mcuRemoteUpdateCapablePresent",
                    "mcuRemoteUpdateCapable");
            if (capability == null) {
                throw permanent(
                        "v4 remote-update capability is missing");
            }
            payload.put("mcuRemoteUpdateCapable", capability);
        }
        payload.put(
                "sensorsHealthy",
                bool(wire, "sensorsHealthy"));
        payload.put(
                "camerasCaptureHealthy",
                bool(wire, "camerasCaptureHealthy"));
        payload.put(
                "cameraUploadHealthy",
                bool(wire, "cameraUploadHealthy"));
        payload.put(
                "deviceEntryUrlStored",
                bool(wire, "deviceEntryUrlStored"));
        payload.put(
                "deviceEntryUrlSha256",
                pattern(wire, "deviceEntryUrlSha256", SHA256));
        payload.put("mcuSimulated", bool(wire, "mcuSimulated"));
        payload.put(
                "camerasSimulated",
                bool(wire, "camerasSimulated"));
        payload.put(
                "verifiedPortCount",
                requiredIntegerInRange(wire, "verifiedPortCount", 1, 6));
        payload.put(
                "verifiedCameraCount",
                requiredIntegerInRange(
                        wire, "verifiedCameraCount", 0, 16));
        payload.put(
                "sensorSampleSha256",
                pattern(wire, "sensorSampleSha256", SHA256));
        payload.put(
                "cameraCaptureSha256",
                pattern(wire, "cameraCaptureSha256", SHA256));
        payload.put(
                "cameraUploadSha256",
                pattern(wire, "cameraUploadSha256", SHA256));
        return payload;
    }

    private static Map<String, Object> runtimePort(
            JsonNode wire,
            boolean fixedFrameCompatibility) {
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
        boolean stableKindValid = fixedFrameCompatibility
                ? "LAST_OBSERVED".equals(valueKind)
                : "STABLE_WINDOW_MEAN".equals(valueKind);
        if (valueAvailable
                != (port.get("reportedWeightGrams") != null)
                || (!valueAvailable && !"NONE".equals(valueKind))
                || ("STABLE".equals(measurementStatus)
                && (!valueAvailable
                || !stableKindValid))) {
            throw permanent(
                    "runtime weight availability fields differ");
        }
        long sampleCount = (Long) port.get("weightSampleCount");
        if (fixedFrameCompatibility
                && "STABLE".equals(measurementStatus)
                && (sampleCount < 0 || sampleCount > 1)) {
            throw permanent(
                    "fixed-frame runtime weight sample count differs");
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

    private static Map<String, Object> remoteSupportStatusPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "sessionUid",
                pattern(wire, "sessionUid", UUID_V4));
        String state = enumText(
                integer(wire, "state"),
                Map.of(
                        1L, "CONNECTING",
                        2L, "OPEN",
                        3L, "CLOSED",
                        4L, "FAILED",
                        5L, "EXPIRED"),
                "state");
        payload.put("state", state);
        payload.put(
                "remotePort",
                requiredIntegerInRange(
                        wire, "remotePort", 22011, 22014));
        String failureCode = nullablePresenceText(
                wire,
                "failureCodePresent",
                "failureCode",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        if ("FAILED".equals(state) && failureCode == null) {
            throw permanent(
                    "failed remote support status lacks failureCode");
        }
        payload.put("failureCode", failureCode);
        return payload;
    }

    private static Map<String, Object> factorySealCompletedPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        long schemaVersion = integer(
                wire, "sealCompletionSchemaVersion");
        if (schemaVersion != 1L && schemaVersion != 2L) {
            throw permanent(
                    "sealCompletionSchemaVersion has an unsupported "
                            + "enum value");
        }
        payload.put("sealCompletionSchemaVersion", schemaVersion);
        payload.put("hardwareSn", text(wire, "hardwareSn", 64));
        payload.put(
                "authorizationCommandUid",
                pattern(wire, "authorizationCommandUid", UUID_V4));
        payload.put(
                "acceptanceGeneration",
                positiveSafeInteger(wire, "acceptanceGeneration"));
        payload.put(
                "acceptanceEvidenceUid",
                pattern(wire, "acceptanceEvidenceUid", UUID_V4));
        payload.put(
                "acceptanceEvidenceSha256",
                pattern(wire, "acceptanceEvidenceSha256", SHA256));
        payload.put(
                "acceptanceChallengeUid",
                pattern(wire, "acceptanceChallengeUid", UUID_V4));
        payload.put(
                "factoryBagRevision",
                nonNegativeSafeInteger(wire, "factoryBagRevision"));
        payload.put(
                "factoryBagSetSha256",
                pattern(wire, "factoryBagSetSha256", SHA256));
        payload.put(
                "imageReleaseId",
                pattern(wire, "imageReleaseId", "^[\\x21-\\x7e]{1,128}$"));
        payload.put(
                "imageReleaseSha256",
                pattern(wire, "imageReleaseSha256", SHA256));
        payload.put(
                "factoryReportSha256",
                pattern(wire, "factoryReportSha256", SHA256));
        payload.put(
                "authorizationBindingSha256",
                pattern(wire, "authorizationBindingSha256", SHA256));
        payload.put(
                "operatorConfirmationUid",
                pattern(wire, "operatorConfirmationUid", UUID_V4));
        String completionClockQuality;
        String sealedAt;
        String cleanupCompletedAt;
        JsonNode completionClockPresence = wire.get(
                "completionClockQualityPresent");
        if (schemaVersion == 1L) {
            if (completionClockPresence != null
                    && bool(wire, "completionClockQualityPresent")) {
                throw permanent(
                        "factory seal v1 must not carry clock quality");
            }
            completionClockQuality = "SYNCED";
            sealedAt = requiredUtcInstant(wire, "sealedAt");
            cleanupCompletedAt = requiredUtcInstant(
                    wire, "cleanupCompletedAt");
        } else {
            if (completionClockPresence == null
                    || !bool(wire, "completionClockQualityPresent")) {
                throw permanent(
                        "factory seal v2 lacks clock quality");
            }
            completionClockQuality = enumText(
                    integer(wire, "completionClockQuality"),
                    Map.of(
                            1L, "SYNCED",
                            2L, "ESTIMATED",
                            3L, "UNAVAILABLE"),
                    "completionClockQuality");
            sealedAt = nullablePresenceInstant(
                    wire, "sealedAtPresent", "sealedAt");
            cleanupCompletedAt = nullablePresenceInstant(
                    wire,
                    "cleanupCompletedAtPresent",
                    "cleanupCompletedAt");
            boolean trusted = "SYNCED".equals(
                    completionClockQuality);
            if (trusted != (sealedAt != null)
                    || trusted != (cleanupCompletedAt != null)) {
                throw permanent(
                        "factory seal completion clock fields differ");
            }
            payload.put(
                    "completionClockQuality",
                    completionClockQuality);
        }
        payload.put("sealedAt", sealedAt);
        payload.put("cleanupCompletedAt", cleanupCompletedAt);
        Map<String, Object> binding = new LinkedHashMap<>();
        binding.put("commandUid", payload.get("authorizationCommandUid"));
        binding.put("hardwareSn", payload.get("hardwareSn"));
        binding.put(
                "acceptanceGeneration",
                payload.get("acceptanceGeneration"));
        binding.put(
                "acceptanceEvidenceUid",
                payload.get("acceptanceEvidenceUid"));
        binding.put(
                "acceptanceChallengeUid",
                payload.get("acceptanceChallengeUid"));
        binding.put(
                "acceptanceEvidenceSha256",
                payload.get("acceptanceEvidenceSha256"));
        binding.put("factoryBagRevision", payload.get("factoryBagRevision"));
        binding.put("factoryBagSetSha256", payload.get("factoryBagSetSha256"));
        binding.put("imageReleaseId", payload.get("imageReleaseId"));
        binding.put("imageReleaseSha256", payload.get("imageReleaseSha256"));
        binding.put("factoryReportSha256", payload.get("factoryReportSha256"));
        if (!OneNetCanonicalJson.payloadSha256(binding).equals(
                payload.get("authorizationBindingSha256"))) {
            throw permanent(
                    "factory seal authorization binding differs");
        }
        if (sealedAt != null
                && Instant.parse(cleanupCompletedAt).isBefore(
                        Instant.parse(sealedAt))) {
            throw permanent(
                    "factory seal cleanup precedes the seal marker");
        }
        return payload;
    }

    private static Map<String, Object> mcuFirmwareProgressPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "deploymentUid",
                pattern(wire, "deploymentUid", UUID_V4));
        payload.put("updateUid", pattern(wire, "updateUid", UUID_V4));
        payload.put("releaseUid", pattern(wire, "releaseUid", UUID_V4));
        payload.put(
                "source",
                enumText(
                        integer(wire, "source"),
                        Map.of(1L, "CLOUD", 2L, "LOCAL"),
                        "source"));
        String stage = enumText(
                integer(wire, "stage"),
                Map.ofEntries(
                        Map.entry(1L, "QUEUED"),
                        Map.entry(2L, "PREFLIGHT"),
                        Map.entry(3L, "PREPARED"),
                        Map.entry(4L, "FLASHING_TARGET"),
                        Map.entry(5L, "VERIFYING_TARGET"),
                        Map.entry(6L, "ROLLING_BACK"),
                        Map.entry(7L, "VERIFYING_ROLLBACK"),
                        Map.entry(8L, "SUCCEEDED"),
                        Map.entry(9L, "ROLLED_BACK"),
                        Map.entry(10L, "FAILED_LOCKED"),
                        Map.entry(11L, "REJECTED"),
                        Map.entry(12L, "PACKAGE_FETCH_FAILED")),
                "stage");
        payload.put("stage", stage);
        payload.put(
                "firmwareVersion",
                text(wire, "firmwareVersion", 32));
        payload.put(
                "firmwareVersionCode",
                requiredIntegerInRange(
                        wire, "firmwareVersionCode", 1, 4_294_967_295L));
        payload.put(
                "firmwareIdentityHex",
                pattern(
                        wire,
                        "firmwareIdentityHex",
                        "^[0-9a-f]{16}$"));
        payload.put(
                "fixedFrameRevision",
                exactEnum(wire, "fixedFrameRevision", 1, 2L));
        payload.put(
                "targetAttemptCount",
                requiredIntegerInRange(
                        wire, "targetAttemptCount", 0, 3));
        payload.put(
                "rollbackAttemptCount",
                requiredIntegerInRange(
                        wire, "rollbackAttemptCount", 0, 3));
        payload.put("legacyPreflight", bool(wire, "legacyPreflight"));
        payload.put(
                "downgradeAuthorized",
                bool(wire, "downgradeAuthorized"));
        String installedVersion = nullablePresenceText(
                wire,
                "installedFirmwareVersionPresent",
                "installedFirmwareVersion",
                "^.{5,32}$",
                32);
        Long installedVersionCode = nullablePresenceInteger(
                wire,
                "installedFirmwareVersionCPresent",
                "installedFirmwareVersionC",
                true);
        if (installedVersionCode != null
                && installedVersionCode > 4_294_967_295L) {
            throw permanent(
                    "installed firmware version code is outside range");
        }
        String installedIdentity = nullablePresenceText(
                wire,
                "installedFirmwareIdentityPresent",
                "installedFirmwareIdentity",
                "^[0-9a-f]{16}$",
                16);
        boolean installedAllNull = installedVersion == null
                && installedVersionCode == null
                && installedIdentity == null;
        boolean installedAllPresent = installedVersion != null
                && installedVersionCode != null
                && installedIdentity != null;
        if (!installedAllNull && !installedAllPresent) {
            throw permanent(
                    "installed firmware identity presence flags differ");
        }
        payload.put("installedFirmwareVersion", installedVersion);
        payload.put(
                "installedFirmwareVersionCode", installedVersionCode);
        payload.put(
                "installedFirmwareIdentityHex", installedIdentity);
        String errorCode = nullablePresenceText(
                wire,
                "errorCodePresent",
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        boolean failure = "PACKAGE_FETCH_FAILED".equals(stage)
                || "FAILED_LOCKED".equals(stage)
                || "REJECTED".equals(stage);
        if (failure != (errorCode != null)) {
            throw permanent(
                    "firmware failure stage and error code presence differ");
        }
        payload.put("errorCode", errorCode);
        return payload;
    }

    private static Map<String, Object> businessRuntimeProgressPayload(
            JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "deploymentUid", pattern(wire, "deploymentUid", UUID_V4));
        payload.put("updateUid", pattern(wire, "updateUid", UUID_V4));
        payload.put("releaseUid", pattern(wire, "releaseUid", UUID_V4));
        payload.put(
                "versionName",
                pattern(
                        wire,
                        "versionName",
                        "^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$"));
        payload.put(
                "releaseSequence",
                positiveSafeInteger(wire, "releaseSequence"));
        payload.put("packageSha256", pattern(wire, "packageSha256", SHA256));
        String stage = enumText(
                integer(wire, "stage"),
                Map.ofEntries(
                        Map.entry(1L, "RECEIVED"),
                        Map.entry(2L, "DOWNLOADING"),
                        Map.entry(3L, "VERIFYING_PACKAGE"),
                        Map.entry(4L, "PACKAGE_READY"),
                        Map.entry(5L, "WAITING_FOR_IDLE"),
                        Map.entry(6L, "MIGRATING_DATA"),
                        Map.entry(7L, "ACTIVATING"),
                        Map.entry(8L, "VERIFYING_TARGET"),
                        Map.entry(9L, "OBSERVING"),
                        Map.entry(10L, "ROLLING_BACK"),
                        Map.entry(11L, "VERIFYING_ROLLBACK"),
                        Map.entry(12L, "SUCCEEDED"),
                        Map.entry(13L, "ROLLED_BACK"),
                        Map.entry(14L, "DEFERRED"),
                        Map.entry(15L, "REJECTED"),
                        Map.entry(16L, "FAILED_LOCKED"),
                        Map.entry(17L, "DOWNLOAD_AUTHORIZATION_REQUIRED"),
                        Map.entry(18L, "CANCELLED")),
                "stage");
        payload.put("stage", stage);
        payload.put(
                "stageSequence",
                positiveSafeInteger(wire, "stageSequence"));
        payload.put(
                "businessAdmissionState",
                enumText(
                        integer(wire, "businessAdmissionState"),
                        Map.of(
                                1L, "OPEN",
                                2L, "DRAINING",
                                3L, "MAINTENANCE",
                                4L, "LOCKED"),
                        "businessAdmissionState"));
        payload.put(
                "downloadAttemptCount",
                requiredIntegerInRange(
                        wire, "downloadAttemptCount", 0, 10));
        payload.put(
                "targetAttemptCount",
                requiredIntegerInRange(
                        wire, "targetAttemptCount", 0, 10));
        payload.put(
                "rollbackAttemptCount",
                requiredIntegerInRange(
                        wire, "rollbackAttemptCount", 0, 10));
        payload.put("databaseRestored", bool(wire, "databaseRestored"));

        String installedReleaseUid = nullablePresenceText(
                wire,
                "installedReleaseUidPresent",
                "installedReleaseUid",
                UUID_V4,
                36);
        String installedVersionName = nullablePresenceText(
                wire,
                "installedVersionNamePresent",
                "installedVersionName",
                "^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$",
                32);
        Long installedReleaseSequence = nullablePresenceIntegerInRange(
                wire,
                "installedReleaseSequencePresent",
                "installedReleaseSequence",
                1,
                9_007_199_254_740_991L);
        String installedPackageSha256 = nullablePresenceText(
                wire,
                "installedPackageSha256Present",
                "installedPackageSha256",
                SHA256,
                64);
        boolean installedAllNull = installedReleaseUid == null
                && installedVersionName == null
                && installedReleaseSequence == null
                && installedPackageSha256 == null;
        boolean installedAllPresent = installedReleaseUid != null
                && installedVersionName != null
                && installedReleaseSequence != null
                && installedPackageSha256 != null;
        if (!installedAllNull && !installedAllPresent) {
            throw permanent(
                    "business runtime installed identity presence flags differ");
        }
        payload.put("installedReleaseUid", installedReleaseUid);
        payload.put("installedVersionName", installedVersionName);
        payload.put("installedReleaseSequence", installedReleaseSequence);
        payload.put("installedPackageSha256", installedPackageSha256);

        String errorCode = nullablePresenceText(
                wire,
                "errorCodePresent",
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        boolean failure = Set.of(
                "DEFERRED",
                "REJECTED",
                "FAILED_LOCKED",
                "DOWNLOAD_AUTHORIZATION_REQUIRED").contains(stage);
        if (failure != (errorCode != null)) {
            throw permanent(
                    "business runtime failure stage and error code differ");
        }
        payload.put("errorCode", errorCode);
        return payload;
    }

    private static Map<String, Object>
            businessRuntimeCancellationResultPayload(JsonNode wire) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put(
                "deploymentUid", pattern(wire, "deploymentUid", UUID_V4));
        payload.put("updateUid", pattern(wire, "updateUid", UUID_V4));
        payload.put(
                "controlSequence",
                positiveSafeInteger(wire, "controlSequence"));
        String result = enumText(
                integer(wire, "result"),
                Map.of(1L, "CANCELLED", 2L, "TOO_LATE"),
                "result");
        payload.put("result", result);
        payload.put(
                "observedStage",
                enumText(
                        integer(wire, "observedStage"),
                        Map.ofEntries(
                                Map.entry(1L, "RECEIVED"),
                                Map.entry(2L, "VERIFYING_PACKAGE"),
                                Map.entry(3L, "PACKAGE_READY"),
                                Map.entry(4L, "WAITING_FOR_IDLE"),
                                Map.entry(5L, "MIGRATING_DATA"),
                                Map.entry(6L, "ACTIVATING"),
                                Map.entry(7L, "VERIFYING_TARGET"),
                                Map.entry(8L, "OBSERVING"),
                                Map.entry(9L, "ROLLING_BACK"),
                                Map.entry(10L, "VERIFYING_ROLLBACK"),
                                Map.entry(11L, "SUCCEEDED"),
                                Map.entry(12L, "ROLLED_BACK"),
                                Map.entry(13L, "DEFERRED"),
                                Map.entry(14L, "REJECTED"),
                                Map.entry(15L, "FAILED_LOCKED")),
                        "observedStage"));
        String admission = enumText(
                integer(wire, "businessAdmissionState"),
                Map.of(
                        1L, "OPEN",
                        2L, "DRAINING",
                        3L, "MAINTENANCE",
                        4L, "LOCKED"),
                "businessAdmissionState");
        payload.put("businessAdmissionState", admission);
        String errorCode = nullablePresenceText(
                wire,
                "errorCodePresent",
                "errorCode",
                "^[A-Z][A-Z0-9_]{0,63}$",
                64);
        if (("CANCELLED".equals(result)
                && (errorCode != null || !"OPEN".equals(admission)))
                || ("TOO_LATE".equals(result)
                && !"BUSINESS_UPDATE_CANCEL_TOO_LATE".equals(errorCode))) {
            throw permanent(
                    "business runtime cancellation result fields differ");
        }
        payload.put("errorCode", errorCode);
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
        if ("CONFIGURATION_PROGRESS".equals(contract.messageKind())
                && !payload.get("applicationUid").equals(targetUid)) {
            throw permanent(
                    "configuration application target differs from payload");
        }
        if ("DEVICE_COMMAND_OBSERVED".equals(
                contract.messageKind())
                && (event.get("commandUid") == null
                || !event.get("commandUid").equals(targetUid))) {
            throw permanent(
                    "device command target differs from envelope");
        }
        if ("DELIVERY_COMPLETE".equals(contract.messageKind())
                && !payload.get("sessionUid").equals(targetUid)) {
            throw permanent(
                    "delivery session target differs from payload");
        }
        if ("CLEAN_COMPLETE".equals(contract.messageKind())
                && !payload.get("operationUid").equals(targetUid)) {
            throw permanent(
                    "clean operation target differs from payload");
        }
        if ("FULLNESS_SAMPLE_COMPLETE".equals(
                contract.messageKind())
                && !payload.get("detectionUid").equals(targetUid)) {
            throw permanent(
                    "fullness detection target differs from payload");
        }
        if ("BASELINE_MEASUREMENT_COMPLETE".equals(
                contract.messageKind())
                && !payload.get("measurementUid").equals(targetUid)) {
            throw permanent(
                    "baseline measurement target differs from payload");
        }
        if ("FULLNESS_STATE_CHANGED".equals(
                contract.messageKind())
                && (!payload.get("stateChangeUid").equals(targetUid)
                || event.get("commandUid") != null)) {
            throw permanent(
                    "fullness state target differs from payload");
        }
        if (Set.of(
                "PHOTO_STATUS_REPORTED",
                "PHOTO_UPLOAD_GRANT_REQUESTED").contains(
                contract.messageKind())) {
            if (!payload.get("workUid").equals(targetUid)
                    || !payload.get("workType").equals(
                    target.get("type"))
                    || event.get("commandUid") != null) {
                throw permanent(
                        "photo work target differs from payload");
            }
        }
        if ("BUSINESS_CONFIRMATION_RECEIPT".equals(
                contract.messageKind())
                && !payload.get("confirmationUid").equals(targetUid)) {
            throw permanent(
                    "confirmation receipt target differs from payload");
        }
        if ("DEVICE_ACCEPTANCE_EVIDENCE".equals(
                contract.messageKind())
                && event.get("commandUid") == null) {
            throw permanent(
                    "acceptance evidence lacks its platform challenge command");
        }
        if ("REMOTE_SUPPORT_TUNNEL_STATUS".equals(
                contract.messageKind())
                && event.get("commandUid") == null) {
            throw permanent(
                    "remote support status lacks its command or device target");
        }
        if ("MCU_FIRMWARE_UPDATE_PROGRESS".equals(
                contract.messageKind())
                && (!payload.get("deploymentUid").equals(targetUid)
                || ("CLOUD".equals(payload.get("source"))
                && event.get("commandUid") == null))) {
            throw permanent(
                    "MCU firmware progress differs from its deployment target");
        }
        if ("BUSINESS_RUNTIME_UPDATE_PROGRESS".equals(
                contract.messageKind())
                && (!payload.get("deploymentUid").equals(targetUid)
                || event.get("commandUid") == null)) {
            throw permanent(
                    "business runtime progress differs from its deployment target");
        }
        if ("BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT".equals(
                contract.messageKind())
                && (!payload.get("deploymentUid").equals(targetUid)
                || event.get("commandUid") == null)) {
            throw permanent(
                    "business runtime cancellation differs from its deployment target");
        }
        if ("DEVICE_SOFTWARE_STATE_REPORTED".equals(
                contract.messageKind())
                && event.get("commandUid") != null) {
            throw permanent(
                    "device software state must not claim a command target");
        }
        if ("FACTORY_SEAL_COMPLETED".equals(contract.messageKind())
                && (!payload.get("hardwareSn").equals(targetUid)
                || !payload.get("authorizationCommandUid").equals(
                        event.get("commandUid")))) {
            throw permanent(
                    "factory seal completion differs from its authority");
        }
        if ("FACTORY_SEAL_COMPLETED".equals(contract.messageKind())) {
            String eventClockQuality = (String) event.get("clockQuality");
            String completionClockQuality = (String) payload.getOrDefault(
                    "completionClockQuality", "SYNCED");
            if (!eventClockQuality.equals(completionClockQuality)) {
                throw permanent(
                        "factory seal completion clock quality differs");
            }
            if ("SYNCED".equals(eventClockQuality)
                    && !Instant.parse((String) event.get("occurredAt"))
                    .equals(Instant.parse((String) payload.get(
                            "cleanupCompletedAt")))) {
                throw permanent(
                        "factory seal event time differs from completed cleanup");
            }
        }
    }

    private static String occurredAt(
            String messageKind, JsonNode wire) {
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

    private static String requiredUtcInstant(
            JsonNode node, String field) {
        String value = text(node, field, 30);
        if (!value.matches(
                "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        + "(?:\\.[0-9]{1,9})?Z$")) {
            throw permanent(field + " is not a target UTC instant");
        }
        try {
            Instant.parse(value);
        } catch (RuntimeException exception) {
            throw permanent(
                    field + " is not a real instant", exception);
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

    private static String nullablePresenceInstant(
            JsonNode node,
            String presenceField,
            String valueField) {
        String value = nullablePresenceText(
                node,
                presenceField,
                valueField,
                "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        + "(?:\\.[0-9]{1,9})?Z$",
                30);
        if (value == null) {
            return null;
        }
        try {
            Instant.parse(value);
        } catch (RuntimeException exception) {
            throw permanent(
                    valueField + " is not a real instant",
                    exception);
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

    private static Long nullablePresenceIntegerInRange(
            JsonNode node,
            String presenceField,
            String valueField,
            long minimum,
            long maximum) {
        Long value = nullablePresenceInteger(
                node,
                presenceField,
                valueField,
                minimum > 0);
        if (value != null
                && (value < minimum || value > maximum)) {
            throw permanent(
                    valueField + " is outside the target range");
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

    private static long requiredIntegerInRange(
            JsonNode node,
            String field,
            long minimum,
            long maximum) {
        long value = integer(node, field);
        if (value < minimum || value > maximum) {
            throw permanent(
                    field + " is outside the target range");
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

    private static Boolean nullablePresenceBoolean(
            JsonNode node,
            String presenceField,
            String valueField) {
        boolean present = bool(node, presenceField);
        JsonNode value = node.get(valueField);
        if (value != null && !value.isBoolean()) {
            throw permanent(valueField + " must be a boolean");
        }
        return present ? bool(node, valueField) : null;
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

    private record FullnessCompatibilityMeasurement(
            Map<String, Object> measurement) {
    }
}
