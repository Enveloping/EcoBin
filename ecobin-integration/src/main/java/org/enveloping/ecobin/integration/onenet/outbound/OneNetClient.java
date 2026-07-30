package org.enveloping.ecobin.integration.onenet.outbound;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
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
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
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
    private final CosUploadCredentialPort cosUploadCredentialPort;
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
            if ("START_DELIVERY_SESSION".equals(
                    submission.commandType())) {
                // Validate the frozen, credential-free command before the
                // first external STS call.
                projectStartDeliverySession(envelope);
                envelope = attachInitialDeliveryCosGrant(
                        envelope,
                        submission);
            } else if ("PROVIDE_PHOTO_UPLOAD_GRANT".equals(
                    submission.commandType())) {
                envelope = attachPhotoUploadGrant(
                        envelope,
                        submission);
            }
            String identifier;
            Map<String, Object> params;
            if ("APPLY_CONFIGURATION".equals(
                    submission.commandType())) {
                identifier = "applyConfiguration";
                params = projectApplyConfiguration(envelope);
            } else if ("START_DELIVERY_SESSION".equals(
                    submission.commandType())) {
                identifier = "startDeliverySession";
                params = projectStartDeliverySession(envelope);
            } else if ("CONFIRM_EDGE_EVENT".equals(
                    submission.commandType())) {
                identifier = "confirmEdgeEvent";
                params = projectConfirmEdgeEvent(envelope);
            } else if ("PROVIDE_PHOTO_UPLOAD_GRANT".equals(
                    submission.commandType())) {
                identifier = "providePhotoUploadGrant";
                params = projectProvidePhotoUploadGrant(envelope);
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

    private JsonNode attachInitialDeliveryCosGrant(
            JsonNode frozenEnvelope,
            DeviceCommandSubmission submission) {
        ObjectNode envelope =
                (ObjectNode) frozenEnvelope.deepCopy();
        JsonNode payload = requiredObject(envelope, "payload");
        String deploymentCode = requiredMatchingText(
                envelope,
                "deploymentCode",
                "^Dp_[A-Za-z0-9_-]{6,61}$",
                64);
        String sessionUid = requiredUuid(payload, "sessionUid");
        int portNo = Math.toIntExact(
                requiredInteger(
                        payload,
                        "portNo",
                        1,
                        6)
                        .longValue());
        String keyPrefix = "ecobin/"
                + deploymentCode
                + "/delivery-session/"
                + sessionUid
                + "/";
        CosUploadCredential credential =
                cosUploadCredentialPort.issue(
                        submission.hardwareSn(),
                        portNo,
                        keyPrefix);
        ObjectNode grant = objectMapper.createObjectNode();
        grant.put("grantUid", UUID.randomUUID().toString());
        grant.put("tmpSecretId", credential.tmpSecretId());
        grant.put("tmpSecretKey", credential.tmpSecretKey());
        ArrayNode tokenParts =
                grant.putArray("sessionTokenParts");
        splitSessionToken(
                credential.sessionToken())
                .forEach(tokenParts::add);
        grant.put("bucket", credential.bucket());
        grant.put("region", credential.region());
        grant.put("baseUrl", credential.baseUrl());
        grant.put("keyPrefix", keyPrefix);
        grant.put(
                "expiresAt",
                Instant.ofEpochSecond(
                        credential.expiredTime())
                        .toString());
        envelope.set("cosGrant", grant);
        return envelope;
    }

    private static List<String> splitSessionToken(
            String token) {
        if (token == null
                || token.isBlank()
                || token.length() > 4_096) {
            throw new IllegalArgumentException(
                    "COS session token is outside the OneNet contract");
        }
        List<String> parts = new ArrayList<>();
        for (int start = 0;
             start < token.length();
             start += 512) {
            parts.add(token.substring(
                    start,
                    Math.min(start + 512, token.length())));
        }
        return parts;
    }

    private JsonNode attachPhotoUploadGrant(
            JsonNode frozenEnvelope,
            DeviceCommandSubmission submission) {
        ObjectNode envelope =
                (ObjectNode) frozenEnvelope.deepCopy();
        JsonNode payload = requiredObject(envelope, "payload");
        String deploymentCode = requiredMatchingText(
                envelope,
                "deploymentCode",
                "^Dp_[A-Za-z0-9_-]{6,61}$",
                64);
        String workType = requiredText(payload, "workType");
        String workUid = requiredUuid(payload, "workUid");
        String workPath = switch (workType) {
            case "DELIVERY_SESSION" -> "delivery-session";
            case "CLEAN_OPERATION" -> "clean-operation";
            default -> throw new IllegalArgumentException(
                    "photo grant work type is unsupported");
        };
        String keyPrefix = "ecobin/"
                + deploymentCode
                + "/"
                + workPath
                + "/"
                + workUid
                + "/";
        CosUploadCredential credential =
                cosUploadCredentialPort.issue(
                        submission.hardwareSn(),
                        1,
                        keyPrefix);
        ObjectNode grant = objectMapper.createObjectNode();
        grant.put("grantUid", UUID.randomUUID().toString());
        grant.put("tmpSecretId", credential.tmpSecretId());
        grant.put("tmpSecretKey", credential.tmpSecretKey());
        ArrayNode tokenParts =
                grant.putArray("sessionTokenParts");
        splitSessionToken(
                credential.sessionToken())
                .forEach(tokenParts::add);
        grant.put("bucket", credential.bucket());
        grant.put("region", credential.region());
        grant.put("baseUrl", credential.baseUrl());
        grant.put("keyPrefix", keyPrefix);
        grant.put(
                "expiresAt",
                Instant.ofEpochSecond(
                        credential.expiredTime()).toString());
        envelope.set("cosGrant", grant);
        Instant issuedAt = Instant.now();
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put(
                "expiresAt",
                issuedAt.plusSeconds(600).toString());
        return envelope;
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

    private Map<String, Object> projectStartDeliverySession(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        String sessionUid = requiredUuid(payload, "sessionUid");
        String targetUid = requiredUuid(target, "uid");
        if (!"DELIVERY_SESSION".equals(
                requiredText(target, "type"))
                || !sessionUid.equals(targetUid)) {
            throw new IllegalArgumentException(
                    "delivery target must identify the payload session");
        }

        Map<String, Object> scalarFields1 =
                new LinkedHashMap<>();
        scalarFields1.put(
                "schemaVersion",
                requiredInteger(envelope, "schemaVersion", 1, 1));
        scalarFields1.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalarFields1.put("commandType", 1);
        scalarFields1.put(
                "deploymentCode",
                requiredMatchingText(
                        envelope,
                        "deploymentCode",
                        "^Dp_[A-Za-z0-9_-]{6,61}$",
                        64));
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        Instant issuedAt = Instant.parse(issuedAtText);
        Instant expiresAt = Instant.parse(expiresAtText);
        if (!expiresAt.isAfter(issuedAt)) {
            throw new IllegalArgumentException(
                    "delivery command expiry must follow issue time");
        }
        scalarFields1.put("issuedAt", issuedAtText);
        scalarFields1.put("expiresAt", expiresAtText);
        scalarFields1.put(
                "payloadSchemaVersion",
                requiredInteger(
                        envelope,
                        "payloadSchemaVersion",
                        1,
                        1));
        scalarFields1.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        scalarFields1.put("sessionUid", sessionUid);
        scalarFields1.put(
                "portNo",
                requiredInteger(payload, "portNo", 1, 6));
        scalarFields1.put(
                "bagUid",
                requiredUuid(payload, "bagUid"));
        scalarFields1.put(
                "unitPriceTenThousandths",
                requiredInteger(
                        payload,
                        "unitPriceTenThousandths",
                        1,
                        4_294_967_295L));
        scalarFields1.put(
                "continueDeliveryWaitMs",
                requiredInteger(
                        payload,
                        "continueDeliveryWaitMs",
                        1_000,
                        4_294_967_295L));
        scalarFields1.put(
                "negativeWeightThresholdGrams",
                requiredInteger(
                        payload,
                        "negativeWeightThresholdGrams",
                        1,
                        4_294_967_295L));
        scalarFields1.put(
                "deliveryAutoCloseMs",
                requiredInteger(
                        payload,
                        "deliveryAutoCloseMs",
                        1_000,
                        4_294_967_295L));

        Map<String, Object> scalarFields2 =
                new LinkedHashMap<>();
        List<String> sessionTokenParts = new ArrayList<>();
        projectCosGrant(
                envelope,
                scalarFields1,
                scalarFields2,
                sessionTokenParts);

        Map<String, Object> projectedTarget =
                new LinkedHashMap<>();
        projectedTarget.put("type", 1);
        projectedTarget.put("uid", targetUid);

        Map<String, Object> projectedConfig =
                new LinkedHashMap<>();
        projectedConfig.put(
                "version",
                requiredInteger(
                        config,
                        "version",
                        1,
                        9_007_199_254_740_991L));
        projectedConfig.put(
                "contentSha256",
                requiredMatchingText(
                        config,
                        "contentSha256",
                        "^[0-9a-f]{64}$",
                        64));
        projectedConfig.put(
                "mcuPayloadSha256",
                requiredMatchingText(
                        config,
                        "mcuPayloadSha256",
                        "^[0-9a-f]{64}$",
                        64));

        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields1", scalarFields1);
        params.put("scalarFields2", scalarFields2);
        params.put("target", projectedTarget);
        params.put("config", projectedConfig);
        params.put(
                "cosGrantSessionTokenParts",
                sessionTokenParts);
        return params;
    }

    private static void projectCosGrant(
            JsonNode envelope,
            Map<String, Object> scalarFields1,
            Map<String, Object> scalarFields2,
            List<String> sessionTokenParts) {
        JsonNode grant = envelope.get("cosGrant");
        if (grant == null) {
            throw new IllegalArgumentException(
                    "cosGrant must be explicit");
        }
        boolean present = !grant.isNull();
        scalarFields1.put("cosGrantPresent", present);
        if (!present) {
            scalarFields1.put("cosGrantGrantUid", "");
            scalarFields1.put("cosGrantTmpSecretId", "");
            scalarFields1.put("cosGrantTmpSecretKey", "");
            scalarFields1.put("cosGrantBucket", "");
            scalarFields2.put("cosGrantRegion", "");
            scalarFields2.put("cosGrantBaseUrl", "");
            scalarFields2.put("cosGrantKeyPrefix", "");
            scalarFields2.put("cosGrantExpiresAt", "");
            return;
        }
        if (!grant.isObject()) {
            throw new IllegalArgumentException(
                    "cosGrant must be an object or null");
        }
        scalarFields1.put(
                "cosGrantGrantUid",
                requiredUuid(grant, "grantUid"));
        scalarFields1.put(
                "cosGrantTmpSecretId",
                requiredBoundedText(
                        grant, "tmpSecretId", 128));
        scalarFields1.put(
                "cosGrantTmpSecretKey",
                requiredBoundedText(
                        grant, "tmpSecretKey", 128));
        scalarFields1.put(
                "cosGrantBucket",
                requiredBoundedText(grant, "bucket", 128));
        scalarFields2.put(
                "cosGrantRegion",
                requiredBoundedText(grant, "region", 32));
        scalarFields2.put(
                "cosGrantBaseUrl",
                requiredMatchingText(
                        grant,
                        "baseUrl",
                        "^https://[^/?#]+$",
                        256));
        scalarFields2.put(
                "cosGrantKeyPrefix",
                requiredMatchingText(
                        grant,
                        "keyPrefix",
                        "^ecobin/Dp_[A-Za-z0-9_-]+/"
                                + "(delivery-session|clean-operation)/"
                                + "[0-9a-f-]{36}/$",
                        256));
        scalarFields2.put(
                "cosGrantExpiresAt",
                requiredInstant(grant, "expiresAt"));
        JsonNode parts = grant.get("sessionTokenParts");
        if (parts == null || !parts.isArray()
                || parts.isEmpty() || parts.size() > 8) {
            throw new IllegalArgumentException(
                    "cosGrant sessionTokenParts must contain 1 to 8 parts");
        }
        for (JsonNode part : parts) {
            if (!part.isTextual()
                    || part.asText().isBlank()
                    || part.asText().length() > 512) {
                throw new IllegalArgumentException(
                        "cosGrant session token part is invalid");
            }
            sessionTokenParts.add(part.asText());
        }
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

    private Map<String, Object> projectProvidePhotoUploadGrant(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String requestEventUid = requiredUuid(
                payload, "grantRequestEventUid");
        if (!"PHOTO_GRANT_REQUEST".equals(
                requiredText(target, "type"))
                || !requestEventUid.equals(
                requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "photo grant target differs from its request");
        }
        String workType = requiredText(payload, "workType");
        List<String> allowed =
                "DELIVERY_SESSION".equals(workType)
                        ? List.of(
                                "BEFORE_INNER",
                                "BEFORE_OUTER",
                                "AFTER_INNER",
                                "AFTER_OUTER")
                        : "CLEAN_OPERATION".equals(workType)
                        ? List.of(
                                "FIRST_OPEN_INNER",
                                "FIRST_OPEN_OUTER",
                                "FINAL_CLOSE_INNER",
                                "FINAL_CLOSE_OUTER")
                        : List.of();
        JsonNode authorized = payload.get("authorizedSlots");
        if (authorized == null
                || !authorized.isArray()
                || authorized.size() != 4) {
            throw new IllegalArgumentException(
                    "photo grant must authorize all work slots");
        }
        List<Integer> projectedSlots = new ArrayList<>();
        for (int index = 0; index < allowed.size(); index++) {
            if (!authorized.get(index).isTextual()
                    || !allowed.get(index).equals(
                    authorized.get(index).asText())) {
                throw new IllegalArgumentException(
                        "photo grant slots differ from the work contract");
            }
            projectedSlots.add(
                    "DELIVERY_SESSION".equals(workType)
                            ? index + 1
                            : index + 5);
        }

        Map<String, Object> scalar = new LinkedHashMap<>();
        scalar.put(
                "schemaVersion",
                requiredInteger(
                        envelope, "schemaVersion", 1, 1));
        scalar.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalar.put("commandType", 1);
        scalar.put(
                "deploymentCode",
                requiredMatchingText(
                        envelope,
                        "deploymentCode",
                        "^Dp_[A-Za-z0-9_-]{6,61}$",
                        64));
        String issuedAtText = requiredInstant(
                envelope, "issuedAt");
        String expiresAtText = requiredInstant(
                envelope, "expiresAt");
        if (!Instant.parse(expiresAtText).isAfter(
                Instant.parse(issuedAtText))) {
            throw new IllegalArgumentException(
                    "photo grant command expiry must follow issue time");
        }
        scalar.put("issuedAt", issuedAtText);
        scalar.put("expiresAt", expiresAtText);
        scalar.put(
                "payloadSchemaVersion",
                requiredInteger(
                        envelope,
                        "payloadSchemaVersion",
                        1,
                        1));
        scalar.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        scalar.put(
                "grantRequestEventUid",
                requestEventUid);
        scalar.put(
                "workType",
                "DELIVERY_SESSION".equals(workType) ? 1 : 2);
        scalar.put(
                "workUid",
                requiredUuid(payload, "workUid"));

        Map<String, Object> first = new LinkedHashMap<>();
        Map<String, Object> second = new LinkedHashMap<>();
        List<String> sessionTokenParts = new ArrayList<>();
        projectCosGrant(
                envelope,
                first,
                second,
                sessionTokenParts);
        if (!Boolean.TRUE.equals(
                first.remove("cosGrantPresent"))) {
            throw new IllegalArgumentException(
                    "photo grant command requires COS credentials");
        }
        scalar.putAll(first);
        scalar.putAll(second);

        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields", scalar);
        params.put(
                "target",
                Map.of(
                        "type", 1,
                        "uid", requestEventUid));
        params.put("authorizedSlots", projectedSlots);
        params.put(
                "cosGrantSessionTokenParts",
                sessionTokenParts);
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

    private static String requiredBoundedText(
            JsonNode parent,
            String field,
            int maximumLength) {
        JsonNode value = parent.get(field);
        if (value == null
                || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw new IllegalArgumentException(
                    field + " must be bounded text");
        }
        return value.asText();
    }

    private static String requiredMatchingText(
            JsonNode parent,
            String field,
            String pattern,
            int maximumLength) {
        String value = requiredBoundedText(
                parent, field, maximumLength);
        if (!value.matches(pattern)) {
            throw new IllegalArgumentException(
                    field + " has an invalid format");
        }
        return value;
    }

    private static String requiredUuid(
            JsonNode parent, String field) {
        return requiredMatchingText(
                parent,
                field,
                "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                        + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
                36);
    }

    private static Number requiredInteger(
            JsonNode parent,
            String field,
            long minimum,
            long maximum) {
        JsonNode value = parent.get(field);
        if (value == null || !value.isIntegralNumber()) {
            throw new IllegalArgumentException(
                    field + " must be an integer");
        }
        long number = value.longValue();
        if (number < minimum || number > maximum) {
            throw new IllegalArgumentException(
                    field + " is outside the target range");
        }
        return value.numberValue();
    }

    private static String requiredInstant(
            JsonNode parent, String field) {
        String value = requiredMatchingText(
                parent,
                field,
                "^[0-9]{4}-[0-9]{2}-[0-9]{2}T"
                        + "[0-9]{2}:[0-9]{2}:[0-9]{2}"
                        + "(?:\\.[0-9]{1,9})?Z$",
                30);
        try {
            Instant.parse(value);
        } catch (RuntimeException exception) {
            throw new IllegalArgumentException(
                    field + " must be a real UTC instant",
                    exception);
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
