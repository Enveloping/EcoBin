package org.enveloping.ecobin.integration.onenet.outbound;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.device.api.port.CosUploadCredentialPort;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.CosUploadCredential;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.integration.onenet.inbound.OneNetCanonicalJson;
import org.slf4j.MDC;
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
import java.util.HexFormat;
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
    private final OneNetDiagnosticLogger diagnosticLogger;

    /**
     * 把业务事务冻结的命令信封投影成 OneNet 物模型服务调用。
     *
     * <p>OneNet 返回成功只证明平台受理了传输请求，不证明香橙派已落盘、MCU 已动作或
     * 后端业务已完成；后续状态必须由设备命令观察和最终业务事件推进。</p>
     */
    @Override
    public DeviceCommandSubmissionResult submit(
            DeviceCommandSubmission submission) {
        long submissionStartedAt = diagnosticLogger.started();
        try (MDC.MDCCloseable ignoredTask = MDC.putCloseable(
                     "taskUid", submission.taskUid().toString());
             MDC.MDCCloseable ignoredDevice = MDC.putCloseable(
                     "hardwareSn", submission.hardwareSn())) {
            JsonNode envelope =
                    objectMapper.readTree(submission.semanticEnvelopeJson());
            requireEnvelopeIdentity(envelope, submission);
            if ("START_DELIVERY_SESSION".equals(
                    submission.commandType())) {
                // 先验证不含凭证的冻结语义，再调用 STS。临时 COS 凭证只在发送时附加，
                // 不进入稳定摘要，也不会因为续期把同一命令变成另一条业务指令。
                projectStartDeliverySession(envelope);
                envelope = attachInitialDeliveryCosGrant(
                        envelope,
                        submission);
            } else if ("START_CLEAN_OPERATION".equals(
                    submission.commandType())) {
                projectStartCleanOperation(envelope);
                envelope = attachInitialCleanCosGrant(
                        envelope,
                        submission);
            } else if ("RESUME_CLEAN_OPERATION".equals(
                    submission.commandType())) {
                projectResumeCleanOperation(envelope);
                envelope = attachInitialCleanCosGrant(
                        envelope,
                        submission);
            } else if ("PROVIDE_PHOTO_UPLOAD_GRANT".equals(
                    submission.commandType())) {
                envelope = attachPhotoUploadGrant(
                        envelope,
                        submission);
            } else if ("REQUEST_DEVICE_ACCEPTANCE".equals(
                    submission.commandType())) {
                envelope = attachDeviceAcceptanceGrant(
                        envelope,
                        submission);
            } else if ("START_MCU_FIRMWARE_UPDATE".equals(
                    submission.commandType())) {
                validateMcuFirmwareUpdate(envelope, submission);
                envelope = attachMcuFirmwareReadGrant(
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
            } else if ("START_CLEAN_OPERATION".equals(
                    submission.commandType())) {
                identifier = "startCleanOperation";
                params = projectStartCleanOperation(envelope);
            } else if ("END_CLEAN_BEFORE_UNLOCK".equals(
                    submission.commandType())) {
                identifier = "endCleanBeforeUnlock";
                params = projectEndCleanBeforeUnlock(envelope);
            } else if ("RESUME_CLEAN_OPERATION".equals(
                    submission.commandType())) {
                identifier = "resumeCleanOperation";
                params = projectResumeCleanOperation(envelope);
            } else if ("SAMPLE_FULLNESS".equals(
                    submission.commandType())) {
                identifier = "sampleFullness";
                params = projectSampleFullness(envelope);
            } else if ("MEASURE_EMPTY_BAG_BASELINE".equals(
                    submission.commandType())) {
                identifier = "measureEmptyBagBaseline";
                params = projectMeasureEmptyBagBaseline(envelope);
            } else if ("CONFIRM_EDGE_EVENT".equals(
                    submission.commandType())) {
                identifier = "confirmEdgeEvent";
                params = projectConfirmEdgeEvent(envelope);
            } else if ("PROVIDE_PHOTO_UPLOAD_GRANT".equals(
                    submission.commandType())) {
                identifier = "providePhotoUploadGrant";
                params = projectProvidePhotoUploadGrant(envelope);
            } else if ("REQUEST_DEVICE_ACCEPTANCE".equals(
                    submission.commandType())) {
                identifier = "requestDeviceAcceptance";
                params = projectRequestDeviceAcceptance(envelope);
            } else if ("AUTHORIZE_FACTORY_SEAL".equals(
                    submission.commandType())) {
                identifier = "authorizeFactorySeal";
                params = projectAuthorizeFactorySeal(envelope);
            } else if ("SYNC_DEVICE_ENTRY_URL".equals(
                    submission.commandType())) {
                identifier = "syncDeviceEntryUrl";
                params = projectSyncDeviceEntryUrl(envelope);
            } else if ("OPEN_REMOTE_SUPPORT_TUNNEL".equals(
                    submission.commandType())) {
                identifier = "openRemoteSupportTunnel";
                params = projectRemoteSupport(envelope, true);
            } else if ("CLOSE_REMOTE_SUPPORT_TUNNEL".equals(
                    submission.commandType())) {
                identifier = "closeRemoteSupportTunnel";
                params = projectRemoteSupport(envelope, false);
            } else if ("START_MCU_FIRMWARE_UPDATE".equals(
                    submission.commandType())) {
                identifier = "startMcuFirmwareUpdate";
                params = projectMcuFirmwareUpdate(envelope);
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
            DeviceCommandSubmissionResult result = submitWireBody(
                    body, submission, identifier);
            log.info(
                    "[OneNet] reliable command attempt type={} task={} outcome={} http={} externalCode={}",
                    submission.commandType(),
                    submission.taskUid(),
                    result.outcome(),
                    result.httpStatus(),
                    result.externalErrorCode());
            return result;
        } catch (RuntimeException exception) {
            log.warn(
                    "[OneNet] frozen command projection rejected type={} task={}",
                    submission.commandType(),
                    submission.taskUid(),
                    diagnosticLogger.sanitized(exception));
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "COMMAND_PROJECTION",
                    null,
                    "COMMAND_PROJECTION_INVALID",
                    null,
                    exception,
                    submissionStartedAt);
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
        String sessionUid = requiredUuid(payload, "sessionUid");
        int portNo = Math.toIntExact(
                requiredInteger(
                        payload,
                        "portNo",
                        1,
                        6)
                        .longValue());
        String keyPrefix = "ecobin/delivery-session/"
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

    private JsonNode attachInitialCleanCosGrant(
            JsonNode frozenEnvelope,
            DeviceCommandSubmission submission) {
        ObjectNode envelope =
                (ObjectNode) frozenEnvelope.deepCopy();
        JsonNode payload = requiredObject(envelope, "payload");
        String operationUid = requiredUuid(
                payload, "operationUid");
        int portNo = Math.toIntExact(
                requiredInteger(payload, "portNo", 1, 6)
                        .longValue());
        String keyPrefix = "ecobin/clean-operation/"
                + operationUid
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
        ArrayNode tokenParts = grant.putArray(
                "sessionTokenParts");
        splitSessionToken(credential.sessionToken())
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
        String workType = requiredText(payload, "workType");
        String workUid = requiredUuid(payload, "workUid");
        String workPath = switch (workType) {
            case "DELIVERY_SESSION" -> "delivery-session";
            case "CLEAN_OPERATION" -> "clean-operation";
            default -> throw new IllegalArgumentException(
                    "photo grant work type is unsupported");
        };
        String keyPrefix = "ecobin/"
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

    private JsonNode attachDeviceAcceptanceGrant(
            JsonNode frozenEnvelope,
            DeviceCommandSubmission submission) {
        ObjectNode envelope = (ObjectNode) frozenEnvelope.deepCopy();
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode target = requiredObject(envelope, "target");
        String challengeUid = requiredUuid(payload, "challengeUid");
        String deviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        if (!submission.hardwareSn().equals(deviceName)
                || !"DEVICE_ASSET".equals(
                        requiredText(target, "type"))
                || !deviceName.equals(requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "acceptance target must be the authenticated device asset");
        }
        requiredInteger(payload, "expectedPortCount", 1, 6);
        String keyPrefix = "ecobin/device-acceptance/"
                + challengeUid
                + "/";
        CosUploadCredential credential = cosUploadCredentialPort.issue(
                submission.hardwareSn(),
                null,
                keyPrefix);
        ObjectNode grant = objectMapper.createObjectNode();
        grant.put("grantUid", UUID.randomUUID().toString());
        grant.put("tmpSecretId", credential.tmpSecretId());
        grant.put("tmpSecretKey", credential.tmpSecretKey());
        ArrayNode tokenParts = grant.putArray("sessionTokenParts");
        splitSessionToken(credential.sessionToken())
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
        return envelope;
    }

    private void validateMcuFirmwareUpdate(
            JsonNode envelope,
            DeviceCommandSubmission submission) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deploymentUid = requiredUuid(payload, "deploymentUid");
        String releaseUid = requiredUuid(payload, "releaseUid");
        String targetDeviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        if (!submission.hardwareSn().equals(targetDeviceName)
                || !"MCU_FIRMWARE_DEPLOYMENT".equals(
                requiredText(target, "type"))
                || !deploymentUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "MCU firmware target differs from its frozen deployment");
        }
        requiredMatchingText(
                payload,
                "firmwareVersion",
                "^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$",
                32);
        requiredInteger(
                payload, "firmwareVersionCode", 1, 4_294_967_295L);
        requiredMatchingText(
                payload,
                "firmwareIdentityHex",
                "^[0-9a-f]{16}$",
                16);
        String packageSha256 = requiredMatchingText(
                payload, "packageSha256", "^[0-9a-f]{64}$", 64);
        requiredInteger(payload, "packageSize", 1, 131_072);
        String expectedObjectKey = "ecobin/mcu-firmware/"
                + releaseUid
                + "/"
                + packageSha256
                + ".efw";
        if (!expectedObjectKey.equals(
                requiredBoundedText(payload, "objectKey", 512))) {
            throw new IllegalArgumentException(
                    "MCU firmware object key differs from release digest");
        }
        JsonNode reason = payload.get("reason");
        if (reason == null
                || (!reason.isNull()
                && (!reason.isTextual()
                || reason.asText().isBlank()
                || reason.asText().length() > 500))) {
            throw new IllegalArgumentException(
                    "MCU firmware reason must be explicit null or bounded text");
        }
        JsonNode frozenGrant = envelope.get("cosGrant");
        if (frozenGrant == null || !frozenGrant.isNull()) {
            throw new IllegalArgumentException(
                    "frozen MCU firmware command must not persist COS secrets");
        }
    }

    private JsonNode attachMcuFirmwareReadGrant(
            JsonNode frozenEnvelope,
            DeviceCommandSubmission submission) {
        ObjectNode envelope = (ObjectNode) frozenEnvelope.deepCopy();
        JsonNode payload = requiredObject(envelope, "payload");
        String releaseUid = requiredUuid(payload, "releaseUid");
        String keyPrefix = "ecobin/mcu-firmware/" + releaseUid + "/";
        CosUploadCredential credential = cosUploadCredentialPort.issue(
                submission.hardwareSn(), null, keyPrefix);
        Instant issuedAt = Instant.now();
        Instant grantExpiresAt = Instant.ofEpochSecond(
                credential.expiredTime());
        if (!grantExpiresAt.isAfter(issuedAt.plusSeconds(60))) {
            throw new IllegalArgumentException(
                    "MCU firmware COS read grant expires too soon");
        }
        Instant commandExpiresAt = issuedAt.plusSeconds(900);
        if (!grantExpiresAt.isAfter(commandExpiresAt)) {
            commandExpiresAt = grantExpiresAt.minusSeconds(1);
        }

        ObjectNode grant = objectMapper.createObjectNode();
        grant.put("grantUid", UUID.randomUUID().toString());
        grant.put("tmpSecretId", credential.tmpSecretId());
        grant.put("tmpSecretKey", credential.tmpSecretKey());
        ArrayNode tokenParts = grant.putArray("sessionTokenParts");
        splitSessionToken(credential.sessionToken()).forEach(tokenParts::add);
        grant.put("bucket", credential.bucket());
        grant.put("region", credential.region());
        grant.put("baseUrl", credential.baseUrl());
        grant.put("keyPrefix", keyPrefix);
        grant.put("expiresAt", grantExpiresAt.toString());
        envelope.set("cosGrant", grant);
        envelope.put("issuedAt", issuedAt.toString());
        envelope.put("expiresAt", commandExpiresAt.toString());
        return envelope;
    }

    private DeviceCommandSubmissionResult submitWireBody(
            Map<String, Object> body,
            DeviceCommandSubmission submission,
            String identifier) {
        long startedAt = diagnosticLogger.started();
        byte[] requestBody;
        try {
            requestBody = objectMapper.writeValueAsBytes(body);
        } catch (RuntimeException exception) {
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "REQUEST_SERIALIZATION",
                    null,
                    "REQUEST_SERIALIZATION_FAILED",
                    null,
                    exception,
                    startedAt);
            return permanent(
                    "REQUEST_SERIALIZATION_FAILED",
                    "OneNet request serialization failed");
        }
        byte[] requestSha256 = sha256(requestBody);
        if (!properties.isConfigured()) {
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "CONFIGURATION_CHECK",
                    null,
                    "ONENET_NOT_CONFIGURED",
                    null,
                    null,
                    startedAt);
            return new DeviceCommandSubmissionResult(
                    DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                    requestSha256,
                    null,
                    null,
                    "ONENET_NOT_CONFIGURED",
                    null,
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
            diagnosticLogger.outboundRequest(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    identifier,
                    url,
                    body,
                    requestSha256);
            ResponseEntity<String> response = restTemplate.postForEntity(
                    url,
                    new HttpEntity<>(body, headers),
                    String.class);
            String responseBody = response.getBody() == null
                    ? ""
                    : response.getBody();
            byte[] responseSha256 = sha256(
                    responseBody.getBytes(StandardCharsets.UTF_8));
            diagnosticLogger.outboundResponse(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    response.getStatusCode().value(),
                    diagnosticExternalCode(responseBody),
                    responseBody,
                    responseSha256,
                    startedAt);
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
            OneNetResponseEvidence responseEvidence = responseEvidence(
                    responseJson,
                    null);
            if (responseJson.path("code").asInt(Integer.MIN_VALUE) == 0) {
                // code=0 到此只跨过“OneNet 平台受理”这一层。可靠任务随后等待香橙派
                // 的受理/物理证据，不允许在这里直接标记投递成功。
                return new DeviceCommandSubmissionResult(
                        DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED,
                        requestSha256,
                        responseSha256,
                        response.getStatusCode().value(),
                        null,
                        responseEvidence.externalRequestId(),
                        responseEvidence.diagnosticOr(
                                "OneNet accepted the service call; device outcome pending"));
            }
            String rawCode = responseJson.path("code").asText();
            String code = safeExternalCode(rawCode);
            DeviceCommandSubmissionResult.Outcome businessOutcome =
                    switch (rawCode) {
                        case "10410" ->
                                DeviceCommandSubmissionResult.Outcome
                                        .TARGET_NOT_FOUND;
                        case "10421" ->
                                DeviceCommandSubmissionResult.Outcome
                                        .TARGET_OFFLINE;
                        case "10500" ->
                                DeviceCommandSubmissionResult.Outcome
                                        .RETRYABLE_FAILURE;
                        default ->
                                DeviceCommandSubmissionResult.Outcome
                                        .PERMANENT_FAILURE;
                    };
            if (businessOutcome
                    == DeviceCommandSubmissionResult.Outcome
                            .RETRYABLE_FAILURE) {
                return retryable(
                        requestSha256,
                        responseSha256,
                        response.getStatusCode().value(),
                        code,
                        responseEvidence.externalRequestId(),
                        responseEvidence.diagnosticOr(
                                "OneNet reported a temporary internal service failure"));
            }
            if (businessOutcome
                    == DeviceCommandSubmissionResult.Outcome
                            .TARGET_NOT_FOUND
                    || businessOutcome
                    == DeviceCommandSubmissionResult.Outcome
                            .TARGET_OFFLINE) {
                return new DeviceCommandSubmissionResult(
                        businessOutcome,
                        requestSha256,
                        responseSha256,
                        response.getStatusCode().value(),
                        code,
                        responseEvidence.externalRequestId(),
                        responseEvidence.diagnosticOr(businessOutcome
                                == DeviceCommandSubmissionResult.Outcome
                                        .TARGET_OFFLINE
                                ? "OneNet reports that the device is offline"
                                : "OneNet cannot resolve the configured device identity"));
            }
            return new DeviceCommandSubmissionResult(
                    DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE,
                    requestSha256,
                    responseSha256,
                    response.getStatusCode().value(),
                    code,
                    responseEvidence.externalRequestId(),
                    responseEvidence.diagnosticOr(
                            "OneNet permanently rejected the service call contract"));
        } catch (RestClientResponseException exception) {
            byte[] responseBody = exception.getResponseBodyAsByteArray();
            int status = exception.getStatusCode().value();
            String responseText = new String(
                    responseBody, StandardCharsets.UTF_8);
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "HTTP_RESPONSE",
                    status,
                    diagnosticExternalCode(responseText),
                    responseText,
                    exception,
                    startedAt);
            DeviceCommandSubmissionResult.Outcome outcome =
                    retryableHttpStatus(status)
                            ? DeviceCommandSubmissionResult.Outcome
                                    .RETRYABLE_FAILURE
                            : DeviceCommandSubmissionResult.Outcome
                                    .PERMANENT_FAILURE;
            OneNetResponseEvidence responseEvidence = responseEvidence(
                    responseText,
                    "OneNet HTTP request failed");
            return new DeviceCommandSubmissionResult(
                    outcome,
                    requestSha256,
                    responseBody.length == 0 ? null : sha256(responseBody),
                    status,
                    "ONENET_HTTP_" + status,
                    responseEvidence.externalRequestId(),
                    responseEvidence.diagnosticOr(
                            "OneNet HTTP request failed"));
        } catch (RestClientException exception) {
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "HTTP_TRANSPORT",
                    null,
                    "ONENET_TRANSPORT_FAILURE",
                    null,
                    exception,
                    startedAt);
            return retryable(
                    requestSha256,
                    null,
                    null,
                    "ONENET_TRANSPORT_FAILURE",
                    "OneNet transport is temporarily unavailable");
        } catch (RuntimeException exception) {
            diagnosticLogger.outboundFailure(
                    submission.taskUid().toString(),
                    submission.hardwareSn(),
                    submission.commandType(),
                    "CLIENT_EXECUTION",
                    null,
                    "ONENET_CLIENT_FAILURE",
                    null,
                    exception,
                    startedAt);
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
        // OneNet 的物模型枚举值 1 表示领域协议版本 "2"。
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
        params.put("target", Map.of(
                "type", configurationTargetCode(
                        requiredText(target, "type")),
                "uid", requiredText(target, "uid")));
        params.put("issuedAt", requiredText(envelope, "issuedAt"));
        params.put("expiresAt", requiredText(envelope, "expiresAt"));
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
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
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalarFields1.put("schemaVersion", 1);
        scalarFields1.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalarFields1.put("commandType", 1);
        scalarFields1.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
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
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalarFields1.put("payloadSchemaVersion", 1);
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

    private Map<String, Object> projectStartCleanOperation(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        String operationUid = requiredUuid(
                payload, "operationUid");
        String targetUid = requiredUuid(target, "uid");
        if (!"CLEAN_OPERATION".equals(
                requiredText(target, "type"))
                || !operationUid.equals(targetUid)) {
            throw new IllegalArgumentException(
                    "clean target must identify the payload operation");
        }

        Map<String, Object> scalarFields1 =
                new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalarFields1.put("schemaVersion", 1);
        scalarFields1.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalarFields1.put("commandType", 1);
        scalarFields1.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
        String issuedAtText = requiredInstant(
                envelope, "issuedAt");
        String expiresAtText = requiredInstant(
                envelope, "expiresAt");
        if (!Instant.parse(expiresAtText).isAfter(
                Instant.parse(issuedAtText))) {
            throw new IllegalArgumentException(
                    "clean command expiry must follow issue time");
        }
        scalarFields1.put("issuedAt", issuedAtText);
        scalarFields1.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalarFields1.put("payloadSchemaVersion", 1);
        scalarFields1.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        scalarFields1.put("operationUid", operationUid);
        scalarFields1.put(
                "portNo",
                requiredInteger(payload, "portNo", 1, 6));
        JsonNode oldBagUid = payload.get("oldBagUid");
        boolean oldBagPresent = oldBagUid != null
                && !oldBagUid.isNull();
        scalarFields1.put("oldBagUidPresent", oldBagPresent);
        scalarFields1.put(
                "oldBagUid",
                oldBagPresent
                        ? requiredUuid(payload, "oldBagUid")
                        : "");
        JsonNode oldBaseline = payload.get(
                "oldBaselineWeightGrams");
        boolean oldBaselinePresent = oldBaseline != null
                && !oldBaseline.isNull();
        scalarFields1.put(
                "oldBaselineWeightGramsPresent",
                oldBaselinePresent);
        scalarFields1.put(
                "oldBaselineWeightGrams",
                oldBaselinePresent
                        ? requiredInteger(
                                payload,
                                "oldBaselineWeightGrams",
                                Integer.MIN_VALUE,
                                Integer.MAX_VALUE)
                        : Integer.MIN_VALUE);
        scalarFields1.put(
                "newBagUid",
                requiredUuid(payload, "newBagUid"));
        scalarFields1.put(
                "operationWindowMs",
                requiredInteger(
                        payload,
                        "operationWindowMs",
                        1,
                        4_294_967_295L));
        scalarFields1.put(
                "recoveryGeneration",
                requiredInteger(
                        payload,
                        "recoveryGeneration",
                        0,
                        0).intValue() + 1);

        Map<String, Object> scalarFields2 =
                new LinkedHashMap<>();
        List<String> sessionTokenParts = new ArrayList<>();
        projectCosGrant(
                envelope,
                scalarFields1,
                scalarFields2,
                sessionTokenParts);
        // The generated clean-service schema places these two longer
        // credential fields in scalarFields2 (delivery uses scalarFields1).
        scalarFields2.put(
                "cosGrantTmpSecretKey",
                scalarFields1.remove("cosGrantTmpSecretKey"));
        scalarFields2.put(
                "cosGrantBucket",
                scalarFields1.remove("cosGrantBucket"));

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
        params.put("target", Map.of(
                "type", 1,
                "uid", targetUid));
        params.put("config", projectedConfig);
        params.put(
                "cosGrantSessionTokenParts",
                sessionTokenParts);
        return params;
    }

    private Map<String, Object> projectEndCleanBeforeUnlock(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String operationUid = requiredUuid(payload, "operationUid");
        if (!"CLEAN_OPERATION".equals(requiredText(target, "type"))
                || !operationUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "end-clean target must identify the payload operation");
        }
        String issuedAt = requiredInstant(envelope, "issuedAt");
        String expiresAt = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAt).isAfter(Instant.parse(issuedAt))) {
            throw new IllegalArgumentException(
                    "end-clean command expiry must follow issue time");
        }
        Map<String, Object> params = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put(
                "targetDeviceName",
                requiredBoundedText(envelope, "targetDeviceName", 64));
        params.put("target", Map.of("type", 1, "uid", operationUid));
        params.put("issuedAt", issuedAt);
        params.put("expiresAt", expiresAt);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
        params.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        params.put("operationUid", operationUid);
        params.put("portNo", requiredInteger(payload, "portNo", 1, 6));
        params.put(
                "reason",
                switch (requiredText(payload, "reason")) {
                    case "CLEANER_CANCELLED" -> 1;
                    case "START_AUTH_EXPIRED" -> 2;
                    case "PREUNLOCK_FAILURE" -> 3;
                    default -> throw new IllegalArgumentException(
                            "unsupported end-clean reason");
                });
        if (!envelope.path("cosGrant").isNull()) {
            throw new IllegalArgumentException(
                    "end-clean command must not carry COS credentials");
        }
        params.put("cosGrantPresent", false);
        return params;
    }

    private Map<String, Object> projectResumeCleanOperation(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        String operationUid = requiredUuid(payload, "operationUid");
        if (!"CLEAN_OPERATION".equals(requiredText(target, "type"))
                || !operationUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "resume-clean target must identify the payload operation");
        }
        String issuedAt = requiredInstant(envelope, "issuedAt");
        String expiresAt = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAt).isAfter(Instant.parse(issuedAt))) {
            throw new IllegalArgumentException(
                    "resume-clean command expiry must follow issue time");
        }
        if (!payload.path("originalCleanerConfirmedOnsite").isBoolean()
                || !payload.path("originalCleanerConfirmedOnsite")
                .booleanValue()) {
            throw new IllegalArgumentException(
                    "resume-clean requires the original cleaner onsite");
        }

        Map<String, Object> first = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        first.put("schemaVersion", 1);
        first.put("commandUid", requiredUuid(envelope, "commandUid"));
        first.put("commandType", 1);
        first.put(
                "targetDeviceName",
                requiredBoundedText(envelope, "targetDeviceName", 64));
        first.put("issuedAt", issuedAt);
        first.put("expiresAt", expiresAt);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        first.put("payloadSchemaVersion", 1);
        first.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        first.put("operationUid", operationUid);
        first.put("portNo", requiredInteger(payload, "portNo", 1, 6));
        first.put("newBagUid", requiredUuid(payload, "newBagUid"));
        first.put(
                "operationWindowMs",
                requiredInteger(
                        payload,
                        "operationWindowMs",
                        1,
                        4_294_967_295L));
        first.put(
                "recoveryGeneration",
                requiredInteger(
                        payload,
                        "recoveryGeneration",
                        1,
                        4_294_967_295L));
        first.put("originalCleanerConfirmedOnsite", true);

        Map<String, Object> second = new LinkedHashMap<>();
        List<String> tokenParts = new ArrayList<>();
        projectCosGrant(envelope, first, second, tokenParts);
        first.put("cosGrantRegion", second.remove("cosGrantRegion"));

        Map<String, Object> projectedConfig = new LinkedHashMap<>();
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
        params.put("scalarFields1", first);
        params.put("scalarFields2", second);
        params.put("target", Map.of("type", 1, "uid", operationUid));
        params.put("config", projectedConfig);
        params.put("cosGrantSessionTokenParts", tokenParts);
        return params;
    }

    private Map<String, Object> projectMeasureEmptyBagBaseline(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        String measurementUid = requiredUuid(payload, "measurementUid");
        if (!"BASELINE_MEASUREMENT".equals(
                requiredText(target, "type"))
                || !measurementUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "baseline target must identify the payload measurement");
        }
        String issuedAt = requiredInstant(envelope, "issuedAt");
        String expiresAt = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAt).isAfter(Instant.parse(issuedAt))) {
            throw new IllegalArgumentException(
                    "baseline command expiry must follow issue time");
        }
        if (!payload.path("emptyBagConfirmed").isBoolean()
                || !payload.path("emptyBagConfirmed").booleanValue()) {
            throw new IllegalArgumentException(
                    "baseline command requires a confirmed empty bag");
        }
        Map<String, Object> params = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put(
                "targetDeviceName",
                requiredBoundedText(envelope, "targetDeviceName", 64));
        params.put("target", Map.of("type", 1, "uid", measurementUid));
        params.put("issuedAt", issuedAt);
        params.put("expiresAt", expiresAt);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
        params.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        params.put("measurementUid", measurementUid);
        params.put("portNo", requiredInteger(payload, "portNo", 1, 6));
        params.put("bagUid", requiredUuid(payload, "bagUid"));
        params.put("emptyBagConfirmed", true);
        params.put(
                "measurementTimeoutMs",
                requiredInteger(
                        payload,
                        "measurementTimeoutMs",
                        1_000,
                        6_000));
        params.put(
                "config",
                Map.of(
                        "version",
                        requiredInteger(
                                config,
                                "version",
                                1,
                                9_007_199_254_740_991L),
                        "contentSha256",
                        requiredMatchingText(
                                config,
                                "contentSha256",
                                "^[0-9a-f]{64}$",
                                64),
                        "mcuPayloadSha256",
                        requiredMatchingText(
                                config,
                                "mcuPayloadSha256",
                                "^[0-9a-f]{64}$",
                                64)));
        if (!envelope.path("cosGrant").isNull()) {
            throw new IllegalArgumentException(
                    "baseline command must not carry COS credentials");
        }
        params.put("cosGrantPresent", false);
        return params;
    }

    private Map<String, Object> projectSampleFullness(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        JsonNode config = requiredObject(payload, "config");
        String detectionUid =
                requiredUuid(payload, "detectionUid");
        if (!"FULLNESS_DETECTION".equals(
                requiredText(target, "type"))
                || !detectionUid.equals(
                requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "fullness target must identify the payload detection");
        }

        Map<String, Object> scalar = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalar.put("schemaVersion", 1);
        scalar.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalar.put("commandType", 1);
        scalar.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
        String issuedAt = requiredInstant(
                envelope,
                "issuedAt");
        String expiresAt = requiredInstant(
                envelope,
                "expiresAt");
        if (!Instant.parse(expiresAt).isAfter(
                Instant.parse(issuedAt))) {
            throw new IllegalArgumentException(
                    "fullness command expiry must follow issue time");
        }
        scalar.put("issuedAt", issuedAt);
        scalar.put("expiresAt", expiresAt);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalar.put("payloadSchemaVersion", 1);
        scalar.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        scalar.put("detectionUid", detectionUid);
        scalar.put(
                "portNo",
                requiredInteger(payload, "portNo", 1, 6));
        scalar.put(
                "sampleRole",
                switch (requiredText(payload, "sampleRole")) {
                    case "INITIAL" -> 1;
                    case "CONFIRMATION" -> 2;
                    case "MANUAL_RECHECK" -> 3;
                    default -> throw new IllegalArgumentException(
                            "unsupported fullness sample role");
                });
        scalar.put(
                "triggerType",
                switch (requiredText(payload, "triggerType")) {
                    case "DELIVERY_COMPLETE" -> 1;
                    case "CLEAN_COMPLETE" -> 2;
                    case "MANUAL_RECHECK" -> 3;
                    default -> throw new IllegalArgumentException(
                            "unsupported fullness trigger type");
                });
        scalar.put(
                "fullnessMode",
                fullnessModeCode(
                        requiredText(payload, "fullnessMode")));
        JsonNode baseline =
                payload.get("currentBaselineWeightGrams");
        boolean baselinePresent =
                baseline != null && !baseline.isNull();
        scalar.put(
                "currentBaselineWeightGramPresent",
                baselinePresent);
        scalar.put(
                "currentBaselineWeightGram",
                baselinePresent
                        ? requiredInteger(
                        payload,
                        "currentBaselineWeightGrams",
                        Integer.MIN_VALUE,
                        Integer.MAX_VALUE)
                        : 0);
        scalar.put(
                "configuredFullWeightGrams",
                requiredInteger(
                        payload,
                        "configuredFullWeightGrams",
                        1,
                        4_294_967_295L));
        scalar.put(
                "settleWaitMs",
                requiredInteger(
                        payload,
                        "settleWaitMs",
                        0,
                        4_294_967_295L));
        scalar.put(
                "measurementTimeoutMs",
                requiredInteger(
                        payload,
                        "measurementTimeoutMs",
                        1_000,
                        6_000));
        scalar.put("cosGrantPresent", false);

        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields", scalar);
        params.put(
                "target",
                Map.of(
                        "type",
                        1,
                        "uid",
                        detectionUid));
        params.put("config", config);
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
                        "^ecobin/(delivery-session|clean-operation|"
                                + "device-acceptance|mcu-firmware)/"
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
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalar.put("schemaVersion", 1);
        scalar.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalar.put("commandType", 1);
        scalar.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
        scalar.put("issuedAt", requiredText(envelope, "issuedAt"));
        scalar.put("expiresAt", requiredText(envelope, "expiresAt"));
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalar.put("payloadSchemaVersion", 1);
        scalar.put(
                "payloadSha256",
                confirmationTransportPayloadSha256(envelope, payload));
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

    private static String confirmationTransportPayloadSha256(
            JsonNode envelope,
            JsonNode payload) {
        JsonNode effectKindNode = payload.get("effectKind");
        if (effectKindNode == null
                || !effectKindNode.isTextual()
                || !("BASELINE_ESTABLISHED".equals(
                        effectKindNode.asText())
                || "BASELINE_RETRY_REQUIRED".equals(
                        effectKindNode.asText()))) {
            return requiredText(envelope, "payloadSha256");
        }

        Map<String, Object> normalized = new LinkedHashMap<>();
        normalized.put(
                "confirmationUid",
                requiredText(payload, "confirmationUid"));
        normalized.put(
                "originalEventUid",
                requiredText(payload, "originalEventUid"));
        normalized.put(
                "originalPayloadSha256",
                requiredText(payload, "originalPayloadSha256"));
        normalized.put("outcome", requiredText(payload, "outcome"));
        normalized.put("effectKind", "UPDATED");
        normalized.put(
                "processedAt",
                requiredText(payload, "processedAt"));

        JsonNode references = payload.get("resultReferences");
        if (references == null || !references.isArray()) {
            throw new IllegalArgumentException(
                    "resultReferences must be an array");
        }
        List<Map<String, Object>> normalizedReferences =
                new ArrayList<>();
        for (JsonNode reference : references) {
            normalizedReferences.add(Map.of(
                    "type", requiredText(reference, "type"),
                    "key", requiredText(reference, "key")));
        }
        normalized.put("resultReferences", normalizedReferences);
        normalized.put(
                "errorCode",
                nullableText(payload, "errorCode"));
        normalized.put(
                "quarantineUid",
                nullableText(payload, "quarantineUid"));
        return OneNetCanonicalJson.payloadSha256(normalized);
    }

    private static String nullableText(
            JsonNode object,
            String field) {
        JsonNode value = object.get(field);
        return value == null || value.isNull()
                ? null
                : requiredText(object, field);
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
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalar.put("schemaVersion", 1);
        scalar.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalar.put("commandType", 1);
        scalar.put(
                "targetDeviceName",
                requiredBoundedText(
                        envelope, "targetDeviceName", 64));
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
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalar.put("payloadSchemaVersion", 1);
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

    private Map<String, Object> projectRequestDeviceAcceptance(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        if (!"DEVICE_ASSET".equals(requiredText(target, "type"))
                || !deviceName.equals(requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "acceptance target differs from targetDeviceName");
        }
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAtText).isAfter(
                Instant.parse(issuedAtText))) {
            throw new IllegalArgumentException(
                    "acceptance command expiry must follow issue time");
        }

        Map<String, Object> scalarFields1 = new LinkedHashMap<>();
        // OneNet represents the domain constant "2" as local enum code 1.
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalarFields1.put("schemaVersion", 1);
        scalarFields1.put(
                "commandUid",
                requiredUuid(envelope, "commandUid"));
        scalarFields1.put("commandType", 1);
        scalarFields1.put("targetDeviceName", deviceName);
        scalarFields1.put("issuedAt", issuedAtText);
        scalarFields1.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalarFields1.put("payloadSchemaVersion", 1);
        scalarFields1.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        scalarFields1.put(
                "challengeUid",
                requiredUuid(payload, "challengeUid"));
        scalarFields1.put(
                "expectedPortCount",
                requiredInteger(payload, "expectedPortCount", 1, 6));
        scalarFields1.put(
                "factoryBagRevision",
                requiredInteger(
                        payload,
                        "factoryBagRevision",
                        0,
                        9_007_199_254_740_991L));
        scalarFields1.put(
                "factoryBagSetSha256",
                requiredMatchingText(
                        payload,
                        "factoryBagSetSha256",
                        "^[0-9a-f]{64}$",
                        64));
        putDeviceEntryUrl(payload, scalarFields1);

        Map<String, Object> first = new LinkedHashMap<>();
        Map<String, Object> second = new LinkedHashMap<>();
        List<String> sessionTokenParts = new ArrayList<>();
        projectCosGrant(
                envelope,
                first,
                second,
                sessionTokenParts);
        if (!Boolean.TRUE.equals(first.remove("cosGrantPresent"))) {
            throw new IllegalArgumentException(
                    "acceptance command requires COS credentials");
        }
        scalarFields1.putAll(first);
        // The generated OneNet model caps a struct at twenty members.  The
        // two V52 bag-generation fields pushed acceptance credentials across
        // that boundary: region/baseUrl remain in scalarFields1 while the
        // final keyPrefix/expiresAt pair lives in scalarFields2.
        scalarFields1.put(
                "cosGrantRegion",
                second.remove("cosGrantRegion"));
        scalarFields1.put(
                "cosGrantBaseUrl",
                second.remove("cosGrantBaseUrl"));

        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields1", scalarFields1);
        params.put("scalarFields2", second);
        params.put("target", Map.of("type", 1, "uid", deviceName));
        params.put(
                "cosGrantSessionTokenParts",
                sessionTokenParts);
        return params;
    }

    private Map<String, Object> projectSyncDeviceEntryUrl(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        if (!"DEVICE_ASSET".equals(requiredText(target, "type"))
                || !deviceName.equals(requiredText(target, "uid"))) {
            throw new IllegalArgumentException(
                    "device entry URL target differs from targetDeviceName");
        }
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAtText).isAfter(
                Instant.parse(issuedAtText))) {
            throw new IllegalArgumentException(
                    "device entry URL command expiry must follow issue time");
        }

        Map<String, Object> params = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put("targetDeviceName", deviceName);
        params.put("target", Map.of("type", 1, "uid", deviceName));
        params.put("issuedAt", issuedAtText);
        params.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
        params.put(
                "payloadSha256",
                requiredMatchingText(
                        envelope,
                        "payloadSha256",
                        "^[0-9a-f]{64}$",
                        64));
        putDeviceEntryUrl(payload, params);
        params.put("cosGrantPresent", false);
        return params;
    }

    private Map<String, Object> projectAuthorizeFactorySeal(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        if (!"DEVICE_ASSET".equals(requiredText(target, "type"))
                || !deviceName.equals(requiredText(target, "uid"))
                || !deviceName.equals(requiredBoundedText(
                payload, "hardwareSn", 64))) {
            throw new IllegalArgumentException(
                    "factory seal target differs from device identity");
        }
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        if (!Instant.parse(expiresAtText).isAfter(
                Instant.parse(issuedAtText))) {
            throw new IllegalArgumentException(
                    "factory seal expiry must follow issue time");
        }
        String payloadSha256 = requiredMatchingText(
                envelope, "payloadSha256", "^[0-9a-f]{64}$", 64);
        @SuppressWarnings("unchecked")
        Map<String, Object> semanticPayload = objectMapper.convertValue(
                payload, Map.class);
        if (!payloadSha256.equals(
                OneNetCanonicalJson.payloadSha256(semanticPayload))) {
            throw new IllegalArgumentException(
                    "factory seal payload digest differs");
        }
        JsonNode cosGrant = envelope.get("cosGrant");
        if (cosGrant == null || !cosGrant.isNull()) {
            throw new IllegalArgumentException(
                    "factory seal command cannot carry COS credentials");
        }

        Map<String, Object> params = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put("targetDeviceName", deviceName);
        params.put("target", Map.of("type", 1, "uid", deviceName));
        params.put("issuedAt", issuedAtText);
        params.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
        params.put("payloadSha256", payloadSha256);
        requiredInteger(payload, "sealAuthorizationSchemaVersion", 1, 1);
        params.put("sealAuthorizationSchemaVersion", 1);
        params.put("hardwareSn", deviceName);
        params.put(
                "acceptanceGeneration",
                requiredInteger(
                        payload,
                        "acceptanceGeneration",
                        1,
                        9_007_199_254_740_991L));
        params.put(
                "acceptanceEvidenceUid",
                requiredUuid(payload, "acceptanceEvidenceUid"));
        params.put(
                "acceptanceChallengeUid",
                requiredUuid(payload, "acceptanceChallengeUid"));
        params.put(
                "acceptanceEvidenceSha256",
                requiredMatchingText(
                        payload,
                        "acceptanceEvidenceSha256",
                        "^[0-9a-f]{64}$",
                        64));
        params.put(
                "factoryBagRevision",
                requiredInteger(
                        payload,
                        "factoryBagRevision",
                        0,
                        9_007_199_254_740_991L));
        params.put(
                "factoryBagSetSha256",
                requiredMatchingText(
                        payload,
                        "factoryBagSetSha256",
                        "^[0-9a-f]{64}$",
                        64));
        params.put("cosGrantPresent", false);
        return params;
    }

    private static void putDeviceEntryUrl(
            JsonNode payload,
            Map<String, Object> target) {
        String url = requiredBoundedText(
                payload, "deviceEntryUrl", 192);
        if (!url.startsWith("https://")
                || !StandardCharsets.US_ASCII.newEncoder()
                .canEncode(url)) {
            throw new IllegalArgumentException(
                    "deviceEntryUrl must be an ASCII HTTPS URL");
        }
        for (byte current : url.getBytes(StandardCharsets.US_ASCII)) {
            int unsigned = Byte.toUnsignedInt(current);
            if (unsigned < 0x21 || unsigned > 0x7e) {
                throw new IllegalArgumentException(
                        "deviceEntryUrl must contain printable ASCII only");
            }
        }
        String digest = requiredMatchingText(
                payload,
                "deviceEntryUrlSha256",
                "^[0-9a-f]{64}$",
                64);
        if (!MessageDigest.isEqual(
                sha256(url.getBytes(StandardCharsets.US_ASCII)),
                HexFormat.of().parseHex(digest))) {
            throw new IllegalArgumentException(
                    "deviceEntryUrlSha256 does not match deviceEntryUrl");
        }
        target.put("deviceEntryUrl", url);
        target.put("deviceEntryUrlSha256", digest);
    }

    private Map<String, Object> projectRemoteSupport(
            JsonNode envelope,
            boolean opening) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deviceName = requiredBoundedText(
                envelope, "targetDeviceName", 64);
        String sessionUid = requiredUuid(payload, "sessionUid");
        if (!"REMOTE_SUPPORT_SESSION".equals(
                requiredText(target, "type"))
                || !sessionUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "remote support target differs from its session");
        }
        if (payload.size() != (opening ? 3 : 1)) {
            throw new IllegalArgumentException(
                    "remote support payload fields differ from its command");
        }
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        Instant issuedAt = Instant.parse(issuedAtText);
        Instant expiresAt = Instant.parse(expiresAtText);
        long maximumSeconds = opening ? 1800 : 300;
        if (!expiresAt.isAfter(issuedAt)
                || expiresAt.isAfter(
                issuedAt.plusSeconds(maximumSeconds))) {
            throw new IllegalArgumentException(
                    "remote support command lifetime is invalid");
        }
        String payloadSha256 = requiredMatchingText(
                envelope,
                "payloadSha256",
                "^[0-9a-f]{64}$",
                64);
        @SuppressWarnings("unchecked")
        Map<String, Object> semanticPayload = objectMapper.convertValue(
                payload, Map.class);
        if (!payloadSha256.equals(
                OneNetCanonicalJson.payloadSha256(semanticPayload))) {
            throw new IllegalArgumentException(
                    "remote support payload digest differs");
        }

        Map<String, Object> params = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        params.put("schemaVersion", 1);
        params.put("commandUid", requiredUuid(envelope, "commandUid"));
        params.put("commandType", 1);
        params.put("targetDeviceName", deviceName);
        params.put("target", Map.of("type", 1, "uid", sessionUid));
        params.put("issuedAt", issuedAtText);
        params.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        params.put("payloadSchemaVersion", 1);
        params.put("payloadSha256", payloadSha256);
        params.put("sessionUid", sessionUid);
        if (opening) {
            params.put(
                    "remotePort",
                    requiredInteger(
                            payload, "remotePort", 22011, 22014));
            String payloadExpiresAt = requiredInstant(
                    payload, "expiresAt");
            if (!expiresAtText.equals(payloadExpiresAt)) {
                throw new IllegalArgumentException(
                        "remote support payload expiry differs from its envelope");
            }
            params.put("payloadExpiresAt", payloadExpiresAt);
        }
        params.put("cosGrantPresent", false);
        return params;
    }

    private Map<String, Object> projectMcuFirmwareUpdate(
            JsonNode envelope) {
        JsonNode target = requiredObject(envelope, "target");
        JsonNode payload = requiredObject(envelope, "payload");
        String deploymentUid = requiredUuid(payload, "deploymentUid");
        String releaseUid = requiredUuid(payload, "releaseUid");
        if (!"MCU_FIRMWARE_DEPLOYMENT".equals(
                requiredText(target, "type"))
                || !deploymentUid.equals(requiredUuid(target, "uid"))) {
            throw new IllegalArgumentException(
                    "MCU firmware target differs from deployment");
        }
        String issuedAtText = requiredInstant(envelope, "issuedAt");
        String expiresAtText = requiredInstant(envelope, "expiresAt");
        Instant issuedAt = Instant.parse(issuedAtText);
        Instant expiresAt = Instant.parse(expiresAtText);
        if (!expiresAt.isAfter(issuedAt)
                || expiresAt.isAfter(issuedAt.plusSeconds(900))) {
            throw new IllegalArgumentException(
                    "MCU firmware command lifetime must not exceed 15 minutes");
        }
        String payloadSha256 = requiredMatchingText(
                envelope, "payloadSha256", "^[0-9a-f]{64}$", 64);
        @SuppressWarnings("unchecked")
        Map<String, Object> semanticPayload = objectMapper.convertValue(
                payload, Map.class);
        if (!payloadSha256.equals(
                OneNetCanonicalJson.payloadSha256(semanticPayload))) {
            throw new IllegalArgumentException(
                    "MCU firmware payload digest differs");
        }

        Map<String, Object> scalarFields1 = new LinkedHashMap<>();
        requiredInteger(envelope, "schemaVersion", 2, 2);
        scalarFields1.put("schemaVersion", 1);
        scalarFields1.put(
                "commandUid", requiredUuid(envelope, "commandUid"));
        scalarFields1.put("commandType", 1);
        scalarFields1.put(
                "targetDeviceName",
                requiredBoundedText(envelope, "targetDeviceName", 64));
        scalarFields1.put("issuedAt", issuedAtText);
        scalarFields1.put("expiresAt", expiresAtText);
        requiredInteger(envelope, "payloadSchemaVersion", 2, 2);
        scalarFields1.put("payloadSchemaVersion", 1);
        scalarFields1.put("payloadSha256", payloadSha256);
        scalarFields1.put("deploymentUid", deploymentUid);
        scalarFields1.put("releaseUid", releaseUid);
        scalarFields1.put(
                "firmwareVersion",
                requiredMatchingText(
                        payload,
                        "firmwareVersion",
                        "^[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$",
                        32));
        scalarFields1.put(
                "firmwareVersionCode",
                requiredInteger(
                        payload,
                        "firmwareVersionCode",
                        1,
                        4_294_967_295L));
        scalarFields1.put(
                "firmwareIdentityHex",
                requiredMatchingText(
                        payload,
                        "firmwareIdentityHex",
                        "^[0-9a-f]{16}$",
                        16));
        scalarFields1.put(
                "objectKey",
                requiredBoundedText(payload, "objectKey", 512));
        scalarFields1.put(
                "packageSha256",
                requiredMatchingText(
                        payload, "packageSha256", "^[0-9a-f]{64}$", 64));
        scalarFields1.put(
                "packageSize",
                requiredInteger(payload, "packageSize", 1, 131_072));
        JsonNode reason = payload.get("reason");
        boolean reasonPresent = reason != null && !reason.isNull();
        scalarFields1.put("reasonPresent", reasonPresent);
        scalarFields1.put(
                "reason",
                reasonPresent
                        ? requiredBoundedText(payload, "reason", 500)
                        : "");

        Map<String, Object> grantFirst = new LinkedHashMap<>();
        Map<String, Object> scalarFields2 = new LinkedHashMap<>();
        List<String> sessionTokenParts = new ArrayList<>();
        projectCosGrant(
                envelope,
                grantFirst,
                scalarFields2,
                sessionTokenParts);
        if (!Boolean.TRUE.equals(grantFirst.remove("cosGrantPresent"))) {
            throw new IllegalArgumentException(
                    "MCU firmware command requires a COS read grant");
        }
        scalarFields2.put(
                "cosGrantTmpSecretKey",
                grantFirst.remove("cosGrantTmpSecretKey"));
        scalarFields2.put(
                "cosGrantBucket",
                grantFirst.remove("cosGrantBucket"));
        scalarFields1.putAll(grantFirst);
        Instant grantExpiry = Instant.parse(
                (String) scalarFields2.get("cosGrantExpiresAt"));
        if (grantExpiry.isBefore(expiresAt)) {
            throw new IllegalArgumentException(
                    "MCU firmware COS grant expires before the command");
        }

        Map<String, Object> params = new LinkedHashMap<>();
        params.put("scalarFields1", scalarFields1);
        params.put("scalarFields2", scalarFields2);
        params.put("target", Map.of("type", 1, "uid", deploymentUid));
        params.put("cosGrantSessionTokenParts", sessionTokenParts);
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
            // Recover immutable tasks frozen by the former baseline producer.
            // New tasks are constrained to the three contract values above.
            case "BASELINE_ESTABLISHED", "BASELINE_RETRY_REQUIRED" -> 2;
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
            case "PORT_FULLNESS_STATE" -> 8;
            default -> throw new IllegalArgumentException(
                    "unsupported confirmation result reference");
        };
    }

    private static void requireEnvelopeIdentity(
            JsonNode envelope, DeviceCommandSubmission submission) {
        if (!envelope.isObject()
                || envelope.path("schemaVersion").asInt() != 2
                || !submission.commandUid().toString().equals(
                        envelope.path("commandUid").asString())
                || !submission.commandType().equals(
                        envelope.path("commandType").asString())
                || !submission.hardwareSn().equals(
                        envelope.path("targetDeviceName").asString())) {
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
        return retryable(
                requestSha256,
                responseSha256,
                httpStatus,
                externalCode,
                null,
                diagnostic);
    }

    private static DeviceCommandSubmissionResult retryable(
            byte[] requestSha256,
            byte[] responseSha256,
            Integer httpStatus,
            String externalCode,
            String externalRequestId,
            String diagnostic) {
        return new DeviceCommandSubmissionResult(
                DeviceCommandSubmissionResult.Outcome.RETRYABLE_FAILURE,
                requestSha256,
                responseSha256,
                httpStatus,
                externalCode,
                externalRequestId,
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
                null,
                diagnostic);
    }

    private static boolean retryableHttpStatus(int status) {
        return status == 408
                || status == 409
                || status == 425
                || status == 429
                || status >= 500;
    }

    private String diagnosticExternalCode(String responseBody) {
        if (responseBody == null || responseBody.isBlank()) {
            return null;
        }
        try {
            JsonNode response = objectMapper.readTree(responseBody);
            JsonNode code = response.get("code");
            return code == null || code.isNull()
                    ? null : code.asText();
        } catch (RuntimeException ignored) {
            return "INVALID_RESPONSE_ENVELOPE";
        }
    }

    private static String safeExternalCode(String rawCode) {
        if (rawCode != null && rawCode.matches("[A-Za-z0-9._:-]{1,48}")) {
            return "ONENET_" + rawCode;
        }
        return "ONENET_API_REJECTED";
    }

    private OneNetResponseEvidence responseEvidence(
            String responseBody,
            String fallbackDiagnostic) {
        if (responseBody == null || responseBody.isBlank()) {
            return new OneNetResponseEvidence(null, fallbackDiagnostic);
        }
        try {
            return responseEvidence(
                    objectMapper.readTree(responseBody),
                    fallbackDiagnostic);
        } catch (RuntimeException ignored) {
            return new OneNetResponseEvidence(null, fallbackDiagnostic);
        }
    }

    private OneNetResponseEvidence responseEvidence(
            JsonNode response,
            String fallbackDiagnostic) {
        String requestId = null;
        for (String field : new String[]{"request_id", "requestId"}) {
            JsonNode value = response.get(field);
            if (value != null
                    && value.isTextual()
                    && value.asText().matches(
                            "[A-Za-z0-9._:-]{1,128}")) {
                requestId = value.asText();
                break;
            }
        }
        JsonNode message = response.get("msg");
        String sanitizedMessage = message != null && message.isTextual()
                ? diagnosticLogger.sanitizedText(message.asText(), 800)
                : null;
        return new OneNetResponseEvidence(
                requestId,
                sanitizedMessage == null
                        ? fallbackDiagnostic
                        : "OneNet response: " + sanitizedMessage);
    }

    private record OneNetResponseEvidence(
            String externalRequestId,
            String diagnostic) {

        private String diagnosticOr(String fallback) {
            return diagnostic == null || diagnostic.isBlank()
                    ? fallback
                    : diagnostic;
        }
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
