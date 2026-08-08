package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.value.MiniappEntryBaseUrl;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationMutationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.PutMiniappConfigurationRequest;
import org.springframework.stereotype.Service;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;

import java.util.UUID;

/**
 * 机构小程序配置入口。AppSecret 与机构配置保存在同一数据库事务中。
 */
@Service
public class TargetMiniappConfigurationService {

    private final TargetIdentityDirectoryService directory;

    public TargetMiniappConfigurationService(
            TargetIdentityDirectoryService directory) {
        this.directory = directory;
    }

    public MiniappConfigurationView get(
            String tenantCode,
            String organizationCode) {
        MiniappConfigurationMetadata metadata =
                directory.getMiniappConfiguration(
                        tenantCode, organizationCode);
        boolean platform = TargetWebActorContext.required().platform();
        return new MiniappConfigurationView(
                metadata.appId(),
                metadata.displayName(),
                platform ? metadata.appSecret() : null,
                metadata.appSecretConfigured(),
                mask(metadata.appSecret()),
                metadata.entryBaseUrl(),
                metadata.activated(),
                metadata.loginEnabled(),
                metadata.version(),
                metadata.configuredAt(),
                metadata.activatedAt(),
                metadata.updatedAt());
    }

    public MiniappConfigurationMutationView put(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            PutMiniappConfigurationRequest request) {
        requireOperationUid(operationUid);
        requireValidEntryBaseUrl(request.entryBaseUrl());
        String appSecret = normalizeOptionalSecret(request.appSecret());
        MiniappConfigurationMetadata metadata =
                directory.putMiniappConfiguration(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        appSecret);
        return mutation(metadata);
    }

    public MiniappConfigurationMutationView activate(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata after =
                directory.activateMiniappConfiguration(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request);
        return mutation(after);
    }

    public MiniappConfigurationMutationView enableLogin(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata after =
                directory.changeMiniappLogin(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        true);
        return mutation(after);
    }

    public MiniappConfigurationMutationView disableLogin(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata after =
                directory.changeMiniappLogin(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        false);
        return mutation(after);
    }

    private static MiniappConfigurationMutationView mutation(
            MiniappConfigurationMetadata metadata) {
        return new MiniappConfigurationMutationView(
                metadata.appId(),
                metadata.displayName(),
                metadata.appSecretConfigured(),
                mask(metadata.appSecret()),
                metadata.entryBaseUrl(),
                metadata.activated(),
                metadata.loginEnabled(),
                metadata.version(),
                metadata.configuredAt(),
                metadata.activatedAt(),
                metadata.updatedAt());
    }

    private static String normalizeOptionalSecret(String value) {
        if (value == null) {
            return null;
        }
        if (value.isBlank()) {
            throw new TargetApiException(
                    400,
                    "IDENTITY.MINIAPP_CONFIGURATION_INVALID",
                    "AppSecret 不能是空白字符串");
        }
        if (!value.equals(value.trim())) {
            throw new TargetApiException(
                    400,
                    "IDENTITY.MINIAPP_CONFIGURATION_INVALID",
                    "AppSecret 首尾不能包含空白字符");
        }
        return value;
    }

    private static void requireValidEntryBaseUrl(String value) {
        if (value == null) {
            return;
        }
        if (!MiniappEntryBaseUrl.isValid(value)) {
            throw new TargetApiException(
                    400,
                    "IDENTITY.MINIAPP_CONFIGURATION_INVALID",
                    "设备二维码入口必须是完整 HTTPS 地址，且不能包含账号信息、片段或 deviceCode 参数");
        }
    }

    private static void requireOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static String mask(String secret) {
        if (secret == null) {
            return null;
        }
        if (secret.length() <= 8) {
            return "*".repeat(secret.length());
        }
        return secret.substring(0, 4)
                + "*".repeat(Math.min(16, secret.length() - 8))
                + secret.substring(secret.length() - 4);
    }

}
