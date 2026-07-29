package org.enveloping.ecobin.integration.onenet.outbound;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.client.RestTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * 中国移动 OneNet 物联网平台客户端（设备命令下行 = 物模型服务调用）。
 * <p>
 * 仅在 {@code ecobin.external.mode=real} 时装配，并要求
 * {@link OneNetProperties#isConfigured()} 为真；Fake 模式使用无网络替身。
 * 真实实现按标准 OneNET token 鉴权调用「设备服务调用」API。
 * <p>
 * ⚠ 下行链路<strong>本地不测试</strong>（无设备、无下发凭证），鉴权/端点细节联调时校验。
 *
 * @see OneNetTokenGenerator token 算法
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@RequiredArgsConstructor
public class OneNetClient
        implements ReliableDeviceCommandSubmissionPort {

    private final OneNetProperties properties;
    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;

    /**
     * Submits the exact command envelope frozen by the business transaction.
     * A successful platform response is transport evidence only.
     */
    @Override
    public DeviceCommandSubmissionResult submit(
            DeviceCommandSubmission submission) {
        try {
            JsonNode envelope =
                    objectMapper.readTree(submission.semanticEnvelopeJson());
            requireEnvelopeIdentity(envelope, submission);
            String identifier;
            Map<String, Object> params;
            if ("APPLY_CONFIGURATION".equals(
                    submission.commandType())) {
                identifier = "applyConfiguration";
                params = projectApplyConfiguration(envelope);
            } else if ("CONFIRM_EDGE_EVENT".equals(
                    submission.commandType())) {
                identifier = "confirmEdgeEvent";
                params = projectConfirmEdgeEvent(envelope);
            } else {
                return permanent(
                        "COMMAND_TYPE_UNSUPPORTED",
                        "target OneNet Adapter does not support this command type");
            }
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("product_id", properties.getProductId());
            body.put("device_name", submission.hardwareSn());
            body.put("identifier", identifier);
            body.put("params", params);
            DeviceCommandSubmissionResult result = submitWireBody(body);
            log.info(
                    "[OneNet] reliable command attempt type={} task={} outcome={} http={}",
                    submission.commandType(),
                    submission.taskUid(),
                    result.outcome(),
                    result.httpStatus());
            return result;
        } catch (RuntimeException exception) {
            log.warn(
                    "[OneNet] frozen command projection rejected type={} task={}",
                    submission.commandType(),
                    submission.taskUid());
            return permanent(
                    "COMMAND_PROJECTION_INVALID",
                    "frozen command cannot be projected to the target OneNet schema");
        }
    }

    private DeviceCommandSubmissionResult submitWireBody(
            Map<String, Object> body) {
        byte[] requestBody;
        try {
            requestBody = objectMapper.writeValueAsBytes(body);
        } catch (RuntimeException exception) {
            return permanent(
                    "REQUEST_SERIALIZATION_FAILED",
                    "OneNet request serialization failed");
        }
        byte[] requestSha256 = sha256(requestBody);
        if (!properties.isConfigured()) {
            return new DeviceCommandSubmissionResult(
                    DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                    requestSha256,
                    null,
                    null,
                    "ONENET_NOT_CONFIGURED",
                    "real external mode lacks OneNet outbound configuration");
        }
        try {
            String token = OneNetTokenGenerator.generate(
                    properties.getVersion(),
                    "products/" + properties.getProductId(),
                    properties.getAccessKey(),
                    properties.getTokenTtlSeconds());
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set(HttpHeaders.AUTHORIZATION, token);
            String url = properties.getBaseUrl()
                    + properties.getInvokeServicePath();
            ResponseEntity<String> response = restTemplate.postForEntity(
                    url,
                    new HttpEntity<>(body, headers),
                    String.class);
            String responseBody = response.getBody() == null
                    ? ""
                    : response.getBody();
            byte[] responseSha256 = sha256(
                    responseBody.getBytes(StandardCharsets.UTF_8));
            JsonNode responseJson;
            try {
                responseJson = objectMapper.readTree(responseBody);
            } catch (RuntimeException malformed) {
                return retryable(
                        requestSha256,
                        responseSha256,
                        response.getStatusCode().value(),
                        "ONENET_RESPONSE_INVALID",
                        "OneNet returned an invalid response envelope");
            }
            if (responseJson.path("code").asInt(Integer.MIN_VALUE) == 0) {
                return new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                        requestSha256,
                        responseSha256,
                        response.getStatusCode().value(),
                        null,
                        "OneNet accepted the service call; device outcome pending");
            }
            String code = safeExternalCode(responseJson.path("code").asText());
            return retryable(
                    requestSha256,
                    responseSha256,
                    response.getStatusCode().value(),
                    code,
                    "OneNet rejected the service call");
        } catch (RestClientResponseException exception) {
            byte[] responseBody = exception.getResponseBodyAsByteArray();
            int status = exception.getStatusCode().value();
            DeviceCommandSubmissionResult.Outcome outcome =
                    retryableHttpStatus(status)
                            ? DeviceCommandSubmissionResult.Outcome
                                    .RETRYABLE_FAILURE
                            : DeviceCommandSubmissionResult.Outcome
                                    .PERMANENT_FAILURE;
            return new DeviceCommandSubmissionResult(
                    outcome,
                    requestSha256,
                    responseBody.length == 0 ? null : sha256(responseBody),
                    status,
                    "ONENET_HTTP_" + status,
                    "OneNet HTTP request failed");
        } catch (RestClientException exception) {
            return retryable(
                    requestSha256,
                    null,
                    null,
                    "ONENET_TRANSPORT_FAILURE",
                    "OneNet transport is temporarily unavailable");
        } catch (RuntimeException exception) {
            return retryable(
                    requestSha256,
                    null,
                    null,
                    "ONENET_CLIENT_FAILURE",
                    "OneNet client failed before a trusted response");
        }
    }

    private Map<String, Object> projectApplyConfiguration(JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode deviceConfig = requiredObject(payload, "deviceConfig");
        JsonNode config = requiredObject(payload, "config");
        JsonNode ports = payload.path("ports");
        if (!ports.isArray() || ports.isEmpty()) {
            throw new IllegalArgumentException("ports must be a non-empty array");
        }
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("schemaVersion", envelope.path("schemaVersion").asInt());
        params.put("commandUid", requiredText(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put(
                "deploymentCode",
                requiredText(envelope, "deploymentCode"));
        params.put("target", Map.of(
                "type", configurationTargetCode(
                        requiredText(target, "type")),
                "uid", requiredText(target, "uid")));
        params.put("issuedAt", requiredText(envelope, "issuedAt"));
        params.put("expiresAt", requiredText(envelope, "expiresAt"));
        params.put(
                "payloadSchemaVersion",
                envelope.path("payloadSchemaVersion").asInt());
        params.put(
                "payloadSha256",
                requiredText(envelope, "payloadSha256"));
        params.put(
                "applicationUid",
                requiredText(payload, "applicationUid"));
        params.put("config", config);
        params.put("deviceConfig", deviceConfig);
        List<Map<String, Object>> projectedPorts = new ArrayList<>();
        for (JsonNode port : ports) {
            Map<String, Object> projected = new LinkedHashMap<>();
            for (String field : port.propertyNames()) {
                JsonNode value = port.get(field);
                if ("fullnessMode".equals(field)) {
                    projected.put(field, fullnessModeCode(value.asString()));
                } else if ("fullnessSensorKind".equals(field)) {
                    projected.put(field, sensorKindCode(value.asString()));
                } else {
                    projected.put(field, value);
                }
            }
            projectedPorts.add(projected);
        }
        params.put("ports", projectedPorts);
        params.put("cosGrantPresent", false);
        return params;
    }

    private Map<String, Object> projectConfirmEdgeEvent(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        if (!"EDGE_EVENT".equals(requiredText(target, "type"))) {
            throw new IllegalArgumentException(
                    "confirmation target type is invalid");
        }
        Map<String, Object> scalar = new LinkedHashMap<>();
        scalar.put("schemaVersion", envelope.path(
                "schemaVersion").asInt());
        scalar.put(
                "commandUid",
                requiredText(envelope, "commandUid"));
        scalar.put("commandType", 1);
        scalar.put(
                "deploymentCode",
                requiredText(envelope, "deploymentCode"));
        scalar.put("issuedAt", requiredText(envelope, "issuedAt"));
        scalar.put("expiresAt", requiredText(envelope, "expiresAt"));
        scalar.put(
                "payloadSchemaVersion",
                envelope.path("payloadSchemaVersion").asInt());
        scalar.put(
                "payloadSha256",
                requiredText(envelope, "payloadSha256"));
        scalar.put(
                "confirmationUid",
                requiredText(payload, "confirmationUid"));
        scalar.put(
                "originalEventUid",
                requiredText(payload, "originalEventUid"));
        scalar.put(
                "originalPayloadSha256",
                requiredText(payload, "originalPayloadSha256"));
        scalar.put(
                "outcome",
                confirmationOutcomeCode(
                        requiredText(payload, "outcome")));
        putNullableEnum(
                scalar,
                "effectKind",
                payload.get("effectKind"),
                OneNetClient::confirmationEffectCode);
        scalar.put(
                "processedAt",
                requiredText(payload, "processedAt"));
        putNullableText(
                scalar, "errorCode", payload.get("errorCode"));
        putNullableText(
                scalar,
                "quarantineUid",
                payload.get("quarantineUid"));
        scalar.put("cosGrantPresent", false);

        Map<String, Object> projectedTarget =
                new LinkedHashMap<>();
        projectedTarget.put("type", 1);
        projectedTarget.put("uid", requiredText(target, "uid"));
        JsonNode references = payload.get("resultReferences");
        if (references == null || !references.isArray()) {
            throw new IllegalArgumentException(
                    "resultReferences must be an array");
        }
        List<Map<String, Object>> projectedReferences =
                new ArrayList<>();
        for (JsonNode reference : references) {
            projectedReferences.add(Map.of(
                    "type",
                    resultReferenceCode(
                            requiredText(reference, "type")),
                    "key",
                    requiredText(reference, "key")));
        }
        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields", scalar);
        params.put("target", projectedTarget);
        params.put("resultReferences", projectedReferences);
        return params;
    }

    private static void putNullableText(
            Map<String, Object> target,
            String field,
            JsonNode value) {
        boolean present = value != null && !value.isNull();
        target.put(field + "Present", present);
        if (present) {
            if (!value.isTextual() || value.asText().isBlank()) {
                throw new IllegalArgumentException(
                        field + " must be nullable text");
            }
            target.put(field, value.asText());
        } else {
            target.put(field, "");
        }
    }

    private static void putNullableEnum(
            Map<String, Object> target,
            String field,
            JsonNode value,
            java.util.function.ToIntFunction<String> encoder) {
        boolean present = value != null && !value.isNull();
        target.put(field + "Present", present);
        target.put(
                field,
                present ? encoder.applyAsInt(value.asText()) : 1);
    }

    private static int confirmationOutcomeCode(String value) {
        return switch (value) {
            case "BUSINESS_APPLIED" -> 1;
            case "EVENT_QUARANTINED" -> 2;
            default -> throw new IllegalArgumentException(
                    "unsupported confirmation outcome");
        };
    }

    private static int confirmationEffectCode(String value) {
        return switch (value) {
            case "CREATED" -> 1;
            case "UPDATED" -> 2;
            case "NO_ACTION_REQUIRED" -> 3;
            default -> throw new IllegalArgumentException(
                    "unsupported confirmation effect");
        };
    }

    private static int resultReferenceCode(String value) {
        return switch (value) {
            case "DELIVERY_ORDER" -> 1;
            case "CLEAN_RECORD" -> 2;
            case "FULLNESS_DETECTION" -> 3;
            case "BASELINE_MEASUREMENT" -> 4;
            case "CONFIGURATION_APPLICATION" -> 5;
            case "PHOTO_SLOT" -> 6;
            case "DEVICE_FAULT" -> 7;
            default -> throw new IllegalArgumentException(
                    "unsupported confirmation result reference");
        };
    }

    private static void requireEnvelopeIdentity(
            JsonNode envelope, DeviceCommandSubmission submission) {
        if (!envelope.isObject()
                || envelope.path("schemaVersion").asInt() != 1
                || !submission.commandUid().toString().equals(
                        envelope.path("commandUid").asString())
                || !submission.commandType().equals(
                        envelope.path("commandType").asString())) {
            throw new IllegalArgumentException(
                    "frozen envelope identity does not match its command row");
        }
        UUID.fromString(envelope.path("commandUid").asString());
    }

    private static JsonNode requiredObject(JsonNode parent, String field) {
        JsonNode value = parent.path(field);
        if (!value.isObject()) {
            throw new IllegalArgumentException(field + " must be an object");
        }
        return value;
    }

    private static String requiredText(JsonNode parent, String field) {
        String value = parent.path(field).asString();
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(field + " must not be blank");
        }
        return value;
    }

    private static int configurationTargetCode(String targetType) {
        if (!"CONFIGURATION_APPLICATION".equals(targetType)) {
            throw new IllegalArgumentException(
                    "configuration target type is invalid");
        }
        return 1;
    }

    private static int fullnessModeCode(String mode) {
        return switch (mode) {
            case "SENSOR_ONLY" -> 1;
            case "WEIGHT_ONLY" -> 2;
            case "SENSOR_OR_WEIGHT" -> 3;
            default -> throw new IllegalArgumentException(
                    "unsupported fullness mode");
        };
    }

    private static int sensorKindCode(String kind) {
        return switch (kind) {
            case "ULTRASONIC" -> 1;
            case "DIGITAL_INFRARED" -> 2;
            default -> throw new IllegalArgumentException(
                    "unsupported fullness sensor kind");
        };
    }

    private static DeviceCommandSubmissionResult retryable(
            byte[] requestSha256,
            byte[] responseSha256,
            Integer httpStatus,
            String externalCode,
            String diagnostic) {
        return new DeviceCommandSubmissionResult(
                DeviceCommandSubmissionResult.Outcome.RETRYABLE_FAILURE,
                requestSha256,
                responseSha256,
                httpStatus,
                externalCode,
                diagnostic);
    }

    private static DeviceCommandSubmissionResult permanent(
            String externalCode, String diagnostic) {
        return new DeviceCommandSubmissionResult(
                DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                null,
                null,
                null,
                externalCode,
                diagnostic);
    }

    private static boolean retryableHttpStatus(int status) {
        return status == 408
                || status == 409
                || status == 425
                || status == 429
                || status >= 500;
    }

    private static String safeExternalCode(String rawCode) {
        if (rawCode != null && rawCode.matches("[A-Za-z0-9._:-]{1,48}")) {
            return "ONENET_" + rawCode;
        }
        return "ONENET_API_REJECTED";
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", exception);
        }
    }

}
