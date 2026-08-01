package org.enveloping.ecobin.identity.application.directory;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.error.MiniappSecretVaultException;
import org.enveloping.ecobin.identity.api.port.MiniappSecretVaultPort;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationMutationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.MiniappConfigurationView;
import org.enveloping.ecobin.identity.web.v1.directory.MiniappConfigurationModels.PutMiniappConfigurationRequest;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;

/**
 * Keeps the external secret write outside the IAM database transaction.
 */
@Service
public class TargetMiniappConfigurationService {

    private final TargetIdentityDirectoryService directory;
    private final MiniappSecretVaultPort secretVault;

    public TargetMiniappConfigurationService(
            TargetIdentityDirectoryService directory,
            MiniappSecretVaultPort secretVault) {
        this.directory = directory;
        this.secretVault = secretVault;
    }

    public MiniappConfigurationView get(
            String tenantCode,
            String organizationCode) {
        MiniappConfigurationMetadata metadata =
                directory.getMiniappConfiguration(
                        tenantCode, organizationCode);
        String secret = readSecret(metadata.secretReference());
        return new MiniappConfigurationView(
                metadata.appId(),
                metadata.displayName(),
                secret,
                true,
                mask(secret),
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
        directory.requireMiniappManagementAccess(
                tenantCode, organizationCode);
        String appSecret = normalizeOptionalSecret(request.appSecret());
        String secretReference = null;
        if (appSecret != null) {
            try {
                secretReference = secretVault.store(
                        operationUid,
                        sha256(appSecret),
                        appSecret);
            } catch (MiniappSecretVaultException exception) {
                throw translate(exception);
            }
        }
        MiniappConfigurationMetadata metadata =
                directory.putMiniappConfiguration(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        secretReference);
        return mutation(
                metadata,
                readSecret(metadata.secretReference()));
    }

    public MiniappConfigurationMutationView activate(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata before =
                directory.getMiniappConfiguration(
                        tenantCode, organizationCode);
        String secret = readSecret(before.secretReference());
        MiniappConfigurationMetadata after =
                directory.activateMiniappConfiguration(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request);
        return mutation(after, secret);
    }

    public MiniappConfigurationMutationView enableLogin(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata before =
                directory.getMiniappConfiguration(
                        tenantCode, organizationCode);
        String secret = readSecret(before.secretReference());
        MiniappConfigurationMetadata after =
                directory.changeMiniappLogin(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        true);
        return mutation(after, secret);
    }

    public MiniappConfigurationMutationView disableLogin(
            UUID operationUid,
            String tenantCode,
            String organizationCode,
            VersionCommand request) {
        MiniappConfigurationMetadata before =
                directory.getMiniappConfiguration(
                        tenantCode, organizationCode);
        String secret = readSecret(before.secretReference());
        MiniappConfigurationMetadata after =
                directory.changeMiniappLogin(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        request,
                        false);
        return mutation(after, secret);
    }

    private String readSecret(String reference) {
        try {
            return secretVault.read(reference);
        } catch (MiniappSecretVaultException exception) {
            throw translate(exception);
        }
    }

    private static MiniappConfigurationMutationView mutation(
            MiniappConfigurationMetadata metadata,
            String secret) {
        return new MiniappConfigurationMutationView(
                metadata.appId(),
                metadata.displayName(),
                true,
                mask(secret),
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
        return value;
    }

    private static void requireOperationUid(UUID operationUid) {
        if (operationUid == null || operationUid.version() != 4) {
            throw new TargetApiException(
                    400,
                    "COMMON.INVALID_REQUEST",
                    "Idempotency-Key 必须是 UUIDv4");
        }
    }

    private static TargetApiException translate(
            MiniappSecretVaultException exception) {
        if (exception.reason()
                == MiniappSecretVaultException.Reason.IDEMPOTENCY_CONFLICT) {
            return new TargetApiException(
                    409,
                    "COMMON.IDEMPOTENCY_KEY_CONFLICT",
                    "相同操作标识已绑定到不同请求");
        }
        return new TargetApiException(
                503,
                "IDENTITY.MINIAPP_SECRET_UNAVAILABLE",
                "小程序密钥暂不可用",
                true,
                Map.of());
    }

    private static String mask(String secret) {
        if (secret.length() <= 8) {
            return "*".repeat(secret.length());
        }
        return secret.substring(0, 4)
                + "*".repeat(Math.min(16, secret.length() - 8))
                + secret.substring(secret.length() - 4);
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(
                            value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }
}
