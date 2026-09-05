package org.enveloping.ecobin.integration.cos;

import com.qcloud.cos.COSClient;
import com.qcloud.cos.ClientConfig;
import com.qcloud.cos.auth.BasicCOSCredentials;
import com.qcloud.cos.exception.CosServiceException;
import com.qcloud.cos.http.HttpMethodName;
import com.qcloud.cos.model.BucketVersioningConfiguration;
import com.qcloud.cos.model.GeneratePresignedUrlRequest;
import com.qcloud.cos.model.GetObjectRequest;
import com.qcloud.cos.model.ObjectMetadata;
import com.qcloud.cos.model.PutObjectRequest;
import com.qcloud.cos.region.Region;
import jakarta.annotation.PreDestroy;
import org.enveloping.ecobin.device.api.port.BusinessReleaseArtifactStoragePort;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Date;
import java.util.regex.Pattern;

@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
public class CosBusinessReleaseArtifactStorage
        implements BusinessReleaseArtifactStoragePort {

    private static final Pattern OBJECT_KEY = Pattern.compile(
            "^edge-runtime/releases/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
                    + "(?:package\\.tar\\.gz|package\\.sig)$");

    private final BusinessReleaseArtifactProperties properties;
    private final CosProperties photoProperties;
    private volatile COSClient client;

    @Autowired
    public CosBusinessReleaseArtifactStorage(
            BusinessReleaseArtifactProperties properties,
            CosProperties photoProperties) {
        this(properties, photoProperties, null);
    }

    CosBusinessReleaseArtifactStorage(
            BusinessReleaseArtifactProperties properties,
            CosProperties photoProperties,
            COSClient client) {
        this.properties = properties;
        this.photoProperties = photoProperties;
        this.client = client;
    }

    @Override
    public Readiness readiness() {
        Readiness configuration = configurationReadiness();
        if (!configuration.available()) {
            return configuration;
        }
        try {
            requireAtomicCreateBucket(client(), properties.getBucketName());
            return new Readiness(
                    true,
                    "业务发布私有 COS 可访问，且支持制品原子防覆盖");
        } catch (IllegalStateException exception) {
            return new Readiness(false, exception.getMessage());
        } catch (RuntimeException exception) {
            return new Readiness(
                    false,
                    "无法读取业务发布私有 COS 桶配置，请检查网络、桶地域和访问权限");
        }
    }

    private Readiness configurationReadiness() {
        if (!properties.isCosConfigured()) {
            return new Readiness(false, "业务发布私有 COS 尚未完整配置");
        }
        if (properties.getBucketName().equals(photoProperties.getBucketName())) {
            return new Readiness(
                    false,
                    "业务发布必须使用与照片公有读桶不同的私有 COS 存储桶");
        }
        if (properties.isRemoteDispatchEnabled()
                && !properties.isRemoteDispatchConfigured()) {
            return new Readiness(
                    false,
                    "业务程序远程下发已开启，但可信 HTTPS 下载基础位置未完整配置");
        }
        return new Readiness(true, "业务发布私有 COS 基础配置完整");
    }

    @Override
    public void storeImmutable(
            String objectKey,
            Path source,
            String sha256,
            long size) {
        requireObjectKey(objectKey);
        COSClient cos = client();
        String bucket = properties.getBucketName();
        requireAtomicCreateBucket(cos, bucket);
        if (cos.doesObjectExist(bucket, objectKey)) {
            if (isExactObject(cos, bucket, objectKey, sha256, size)) {
                return;
            }
            throw new IllegalStateException("业务发布对象已存在，但内容与本次上传不一致，禁止覆盖");
        }
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(size);
        metadata.addUserMetadata("sha256", sha256);
        metadata.setHeader("x-cos-forbid-overwrite", "true");
        PutObjectRequest request = new PutObjectRequest(
                bucket, objectKey, source.toFile());
        request.setMetadata(metadata);
        try {
            cos.putObject(request);
        } catch (CosServiceException exception) {
            if (exception.getStatusCode() == 409
                    && isExactObject(cos, bucket, objectKey, sha256, size)) {
                return;
            }
            throw exception;
        }
        if (!isExactObject(cos, bucket, objectKey, sha256, size)) {
            throw new IllegalStateException("业务发布对象写入后校验失败");
        }
    }

    @Override
    public void download(String objectKey, Path target) {
        requireObjectKey(objectKey);
        client().getObject(
                new GetObjectRequest(properties.getBucketName(), objectKey),
                target.toFile());
    }

    @Override
    public DownloadAuthorization issueReadAuthorization(
            String objectKey,
            Duration validity) {
        requireObjectKey(objectKey);
        if (!properties.isRemoteDispatchConfigured()) {
            throw new IllegalStateException(
                    "业务程序可信下载基础位置未配置");
        }
        if (validity == null
                || validity.compareTo(Duration.ofMinutes(1)) < 0
                || validity.compareTo(Duration.ofHours(1)) > 0) {
            throw new IllegalArgumentException(
                    "业务发布下载授权有效期必须在 1 分钟到 1 小时之间");
        }
        Instant expiresAt = Instant.now().plus(validity);
        GeneratePresignedUrlRequest request =
                new GeneratePresignedUrlRequest(
                        properties.getBucketName(),
                        objectKey,
                        HttpMethodName.GET);
        request.setExpiration(Date.from(expiresAt));
        String url = client().generatePresignedUrl(request).toExternalForm();
        String expectedPrefix = properties.getDownloadBaseUrl()
                + "/" + objectKey + "?";
        if (!url.startsWith(expectedPrefix)) {
            throw new IllegalStateException(
                    "COS 签发的下载地址不在设备信任的基础位置内");
        }
        return new DownloadAuthorization(url, expiresAt);
    }

    private COSClient client() {
        Readiness readiness = configurationReadiness();
        if (!readiness.available()) {
            throw new IllegalStateException(readiness.message());
        }
        COSClient existing = client;
        if (existing != null) {
            return existing;
        }
        synchronized (this) {
            if (client == null) {
                ClientConfig config = new ClientConfig(
                        new Region(properties.getRegion()));
                client = new COSClient(
                        new BasicCOSCredentials(
                                properties.getSecretId(),
                                properties.getSecretKey()),
                        config);
            }
            return client;
        }
    }

    private static void requireAtomicCreateBucket(
            COSClient cos,
            String bucket) {
        BucketVersioningConfiguration versioning =
                cos.getBucketVersioningConfiguration(bucket);
        if (versioning == null
                || !BucketVersioningConfiguration.OFF.equals(
                versioning.getStatus())) {
            throw new IllegalStateException(
                    "业务发布 COS 桶必须从未启用版本控制，"
                            + "否则无法保证同一路径绝不被覆盖");
        }
    }

    private static boolean isExactObject(
            COSClient cos,
            String bucket,
            String objectKey,
            String sha256,
            long size) {
        ObjectMetadata existing = cos.getObjectMetadata(bucket, objectKey);
        return existing.getContentLength() == size
                && sha256.equals(existing.getUserMetaDataOf("sha256"));
    }

    private static void requireObjectKey(String objectKey) {
        if (objectKey == null || !OBJECT_KEY.matcher(objectKey).matches()) {
            throw new IllegalArgumentException("业务发布对象路径不符合固定规则");
        }
    }

    @PreDestroy
    void shutdown() {
        COSClient existing = client;
        if (existing != null) {
            existing.shutdown();
        }
    }
}
