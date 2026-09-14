package org.enveloping.ecobin.device.application.software;

import org.apache.commons.compress.compressors.gzip.GzipCompressorInputStream;
import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream;
import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.Signature;
import java.security.SignatureException;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Verifies one offline-signed business package without extracting it. */
@Component
public class BusinessReleasePackageVerifier {

    public static final long MAXIMUM_PACKAGE_BYTES = 1_610_612_736L;
    private static final long MAXIMUM_EXPANDED_BYTES = 1_610_612_736L;
    private static final int MAXIMUM_MEMBERS = 20_000;
    private static final int MAXIMUM_METADATA_BYTES = 8 * 1024 * 1024;
    private static final Pattern SHA256_LINE = Pattern.compile(
            "([0-9a-f]{64})  ([^\\r\\n]+)");
    private static final Pattern VERSION = Pattern.compile(
            "^(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\."
                    + "(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?(?:\\+[0-9A-Za-z-]+"
                    + "(?:\\.[0-9A-Za-z-]+)*)?$");
    private static final Pattern GIT_COMMIT = Pattern.compile("^[0-9a-f]{40}$");
    private static final Pattern BITMAP = Pattern.compile("^[0-9a-f]{16}$");
    private static final String APP_ALLOWLIST_SHA256 =
            "a376d6605629faf8a20de0459d1094296ef65cb7574de174bbbae054f6e2804d";
    private static final Set<String> TOP_LEVEL = Set.of(
            "app", "wheelhouse", "migrations", "manifest.env", "release.env",
            "requirements-runtime.txt", "requirements-offline.txt", "SHA256SUMS");
    private static final Set<String> DIRECTORIES = Set.of(
            "", "app", "app/factory_seal", "app/system",
            "wheelhouse", "migrations");
    private static final Set<String> APP_FILES = Set.of(
            "uart2_protocol.py",
            "mcu_action_evidence.py",
            "work_recovery.py",
            "native_result_evidence.py",
            "native_result_report.py",
            "native_delivery_issue_report.py",
            "native_device_entry_url.py",
            "native_delivery_recovery_close.py",
            "native_recovery_close_isolation.py",
            "native_recovery_entry.py",
            "native_recovery_runtime.py",
            "native_business_completion.py",
            "native_business_runtime.py",
            "native_configuration_reload.py",
            "native_control_failure.py",
            "native_issue_completion.py",
            "native_job_rpc.py",
            "mcu_work_query.py",
            "mcu_actuator_handoff.py",
            "mcu_process_handoff.py",
            "mcu_session.py",
            "mcu_result_handoff.py",
            "mcu_configuration.py",
            "uart2_transport.py",
            "business_identity.py",
            "business_control.py",
            "native_fault_control_cli.py",
            "business_message_handler.py",
            "business_outbox_relay.py",
            "camera_capture.py",
            "camera_selection.py",
            "cloud_transport.py",
            "command_processor.py",
            "config.py",
            "cos_photo_uploader.py",
            "device_entry_url_refresh.py",
            "device_identity.py",
            "edge_boot.py",
            "edge_identity.py",
            "edge_store.py",
            "edge_store_prepare.py",
            "fixed_frame_health_recovery.py",
            "fixed_frame_mcu_adapter.py",
            "fixed_frame_mcu_maintenance.py",
            "factory_seal/__init__.py",
            "factory_seal/admission.py",
            "factory_seal/errors.py",
            "factory_seal/validation.py",
            "factory_seal/weight_validation.py",
            "main.py",
            "job_safety.py",
            "local_control.py",
            "local_proxy_cloud_transport.py",
            "onenet_projection_model.json",
            "onenet_wire.py",
            "photo_manager.py",
            "remote_support_control.py",
            "simulated_camera.py",
            "system/__init__.py",
            "trusted_clock.py",
            "uart_link.py",
            "uart_protocol.py",
            "work_manager.py");
    private static final Set<String> MANIFEST_KEYS = Set.of(
            "ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION",
            "ECOBIN_ARTIFACT_KIND",
            "ECOBIN_RELEASE_ID",
            "ECOBIN_VERSION_NAME",
            "ECOBIN_RELEASE_SEQUENCE",
            "ECOBIN_GIT_COMMIT",
            "ECOBIN_PYTHON_SERIES",
            "ECOBIN_TARGET_PLATFORM",
            "ECOBIN_EDGE_SCHEMA_VERSION",
            "ECOBIN_SOURCE_DATE_EPOCH",
            "ECOBIN_BUSINESS_ALLOWLIST_SHA256",
            "ECOBIN_BACKEND_COMMAND_CONTRACT_VERSION",
            "ECOBIN_DEVICE_EVENT_CONTRACT_VERSION",
            "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MAJOR",
            "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MINOR",
            "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MAJOR",
            "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MINOR",
            "ECOBIN_UART_PROTOCOL_FAMILY",
            "ECOBIN_UART_PROTOCOL_MAJOR",
            "ECOBIN_UART_PROTOCOL_MINOR",
            "ECOBIN_UART_REGISTRY_VERSION",
            "ECOBIN_UART_REGISTRY_SHA256",
            "ECOBIN_ONENET_MAPPING_VERSION",
            "ECOBIN_ONENET_MAPPING_SHA256",
            "ECOBIN_REQUIRED_FIXED_FRAME_REVISION",
            "ECOBIN_REQUIRED_MCU_CAPABILITY_BITMAP_HEX",
            "ECOBIN_PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX");

    private final BusinessReleaseSigningKeyPort signingKeys;

    public BusinessReleasePackageVerifier(
            BusinessReleaseSigningKeyPort signingKeys) {
        this.signingKeys = signingKeys;
    }

    public VerifiedRelease verify(
            Path archive,
            Path signatureFile,
            String signingKeyId,
            UUID expectedReleaseUid,
            String expectedVersionName,
            long expectedReleaseSequence) {
        requireRegular(archive, MAXIMUM_PACKAGE_BYTES, "发布包");
        requireRegular(signatureFile, 64, "签名文件");
        try {
            if (Files.size(signatureFile) != 64) {
                throw failure("SIGNATURE_SIZE_INVALID", "签名文件必须恰好为 64 字节");
            }
            String packageSha256 = digest(archive);
            byte[] signatureBytes = Files.readAllBytes(signatureFile);
            verifySignature(archive, signatureBytes, signingKeyId);
            ArchiveContents contents = inspectArchive(archive, expectedReleaseUid);
            verifyInventory(contents);
            Map<String, String> manifest = parseEnv(
                    contents.metadata().get("manifest.env"), "manifest.env");
            if (!manifest.keySet().equals(MANIFEST_KEYS)) {
                throw failure("MANIFEST_KEYS_INVALID", "发布清单字段不完整或包含未知字段");
            }
            VerifiedRelease verified = validateManifest(
                    manifest,
                    expectedReleaseUid,
                    expectedVersionName,
                    expectedReleaseSequence,
                    packageSha256,
                    Files.size(archive));
            Map<String, String> releaseEnvironment = parseEnv(
                    contents.metadata().get("release.env"), "release.env");
            Map<String, String> expectedEnvironment = Map.of(
                    "ECOBIN_EDGE_VERSION", expectedVersionName,
                    "ECOBIN_BUSINESS_RELEASE_ID", expectedReleaseUid.toString(),
                    "ECOBIN_BUSINESS_RELEASE_SEQUENCE",
                    Long.toString(expectedReleaseSequence));
            if (!releaseEnvironment.equals(expectedEnvironment)) {
                throw failure("RELEASE_IDENTITY_INVALID", "运行身份文件与发布记录不一致");
            }
            return verified;
        } catch (VerificationException exception) {
            throw exception;
        } catch (Exception exception) {
            throw failure(
                    "PACKAGE_READ_FAILED",
                    "无法完整读取业务发布包：" + safeMessage(exception));
        }
    }

    private void verifySignature(
            Path archive,
            byte[] signatureBytes,
            String signingKeyId) throws Exception {
        byte[] encodedKey;
        try {
            encodedKey = signingKeys.loadEd25519PublicKey(signingKeyId);
        } catch (Exception exception) {
            throw failure(
                    "SIGNING_KEY_UNAVAILABLE",
                    "找不到或无法读取指定的验签公钥");
        }
        PublicKey publicKey = decodePublicKey(encodedKey);
        Signature verifier = Signature.getInstance("Ed25519");
        try {
            verifier.initVerify(publicKey);
        } catch (Exception exception) {
            throw failure("SIGNING_KEY_INVALID", "验签公钥不是有效的 Ed25519 公钥");
        }
        try (InputStream input = Files.newInputStream(archive)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                if (count > 0) {
                    try {
                        verifier.update(buffer, 0, count);
                    } catch (SignatureException exception) {
                        throw failure("SIGNATURE_INVALID", "发布包签名验证失败");
                    }
                }
            }
        }
        try {
            if (!verifier.verify(signatureBytes)) {
                throw failure("SIGNATURE_INVALID", "发布包签名验证失败");
            }
        } catch (SignatureException exception) {
            throw failure("SIGNATURE_INVALID", "发布包签名验证失败");
        }
    }

    private static PublicKey decodePublicKey(byte[] pem) {
        try {
            String text = new String(pem, StandardCharsets.US_ASCII).trim();
            if (!text.startsWith("-----BEGIN PUBLIC KEY-----")
                    || !text.endsWith("-----END PUBLIC KEY-----")) {
                throw failure(
                        "SIGNING_KEY_INVALID",
                        "验签公钥不是受支持的 PEM 公钥");
            }
            String encoded = text
                    .replace("-----BEGIN PUBLIC KEY-----", "")
                    .replace("-----END PUBLIC KEY-----", "")
                    .replaceAll("\\s", "");
            return KeyFactory.getInstance("Ed25519").generatePublic(
                    new X509EncodedKeySpec(Base64.getDecoder().decode(encoded)));
        } catch (VerificationException exception) {
            throw exception;
        } catch (Exception exception) {
            throw failure("SIGNING_KEY_INVALID", "验签公钥不是有效的 Ed25519 公钥");
        }
    }

    private static ArchiveContents inspectArchive(
            Path archive,
            UUID expectedReleaseUid) throws IOException {
        String expectedRoot = "ecobin-business-" + expectedReleaseUid;
        Set<String> names = new HashSet<>();
        Set<String> directories = new HashSet<>();
        Map<String, String> digests = new LinkedHashMap<>();
        Map<String, byte[]> metadata = new HashMap<>();
        long expanded = 0;
        int members = 0;
        try (InputStream raw = Files.newInputStream(archive);
             GzipCompressorInputStream gzip = new GzipCompressorInputStream(raw);
             TarArchiveInputStream tar = new TarArchiveInputStream(gzip)) {
            TarArchiveEntry entry;
            while ((entry = tar.getNextTarEntry()) != null) {
                members++;
                if (members > MAXIMUM_MEMBERS) {
                    throw failure("ARCHIVE_MEMBER_LIMIT", "发布包文件数量超过上限");
                }
                String canonical = safeArchiveName(entry.getName());
                if (!names.add(canonical)) {
                    throw failure("ARCHIVE_DUPLICATE_MEMBER", "发布包包含重复路径");
                }
                if (!canonical.equals(expectedRoot)
                        && !canonical.startsWith(expectedRoot + "/")) {
                    throw failure("ARCHIVE_ROOT_INVALID", "发布包顶层目录与发布编号不一致");
                }
                String relative = canonical.equals(expectedRoot)
                        ? "" : canonical.substring(expectedRoot.length() + 1);
                if (entry.isDirectory()) {
                    directories.add(relative);
                    continue;
                }
                if (!entry.isFile() || relative.isEmpty()) {
                    throw failure("ARCHIVE_SPECIAL_FILE", "发布包包含链接或特殊文件");
                }
                expanded = Math.addExact(expanded, entry.getSize());
                if (entry.getSize() < 0 || expanded > MAXIMUM_EXPANDED_BYTES) {
                    throw failure("ARCHIVE_EXPANDED_LIMIT", "发布包解压后大小超过上限");
                }
                MessageDigest digest = sha256();
                ByteArrayOutputStream captured = shouldCapture(relative)
                        ? new ByteArrayOutputStream() : null;
                if (captured != null && entry.getSize() > MAXIMUM_METADATA_BYTES) {
                    throw failure("METADATA_TOO_LARGE", "发布包元数据文件超过上限");
                }
                byte[] buffer = new byte[1024 * 1024];
                long actual = 0;
                int count;
                while ((count = tar.read(buffer)) >= 0) {
                    if (count == 0) {
                        continue;
                    }
                    actual += count;
                    digest.update(buffer, 0, count);
                    if (captured != null) {
                        captured.write(buffer, 0, count);
                    }
                }
                if (actual != entry.getSize()) {
                    throw failure("ARCHIVE_TRUNCATED", "发布包中的文件不完整");
                }
                digests.put(relative, HexFormat.of().formatHex(digest.digest()));
                if (captured != null) {
                    metadata.put(relative, captured.toByteArray());
                }
            }
        } catch (ArithmeticException exception) {
            throw failure("ARCHIVE_EXPANDED_LIMIT", "发布包解压后大小超过上限");
        }
        if (members == 0 || !directories.contains("")) {
            throw failure("ARCHIVE_EMPTY", "发布包为空或缺少顶层目录");
        }
        return new ArchiveContents(directories, digests, metadata);
    }

    private static void verifyInventory(ArchiveContents contents) {
        if (!contents.directories().equals(DIRECTORIES)) {
            throw failure(
                    "ARCHIVE_DIRECTORIES_INVALID",
                    "发布包目录不符合固定格式或包含额外目录");
        }
        Set<String> topLevel = new LinkedHashSet<>();
        for (String name : contents.files().keySet()) {
            topLevel.add(firstPart(name));
        }
        for (String name : contents.directories()) {
            if (!name.isEmpty()) {
                topLevel.add(firstPart(name));
            }
        }
        if (!topLevel.equals(TOP_LEVEL)) {
            throw failure("ARCHIVE_LAYOUT_INVALID", "发布包顶层内容不符合固定格式");
        }
        Set<String> appFiles = new HashSet<>();
        List<String> wheelFiles = new ArrayList<>();
        for (String name : contents.files().keySet()) {
            if (name.startsWith("app/")) {
                appFiles.add(name.substring("app/".length()));
            } else if (name.startsWith("wheelhouse/")) {
                wheelFiles.add(name.substring("wheelhouse/".length()));
            } else if (name.startsWith("migrations/")) {
                throw failure("MIGRATIONS_NOT_SUPPORTED", "首版更新不允许携带数据库迁移");
            }
        }
        if (!appFiles.equals(APP_FILES)) {
            throw failure("BUSINESS_ALLOWLIST_INVALID", "业务代码文件不符合固定白名单");
        }
        if (wheelFiles.isEmpty()
                || wheelFiles.stream().anyMatch(name -> name.contains("/")
                || !name.endsWith(".whl"))) {
            throw failure("WHEELHOUSE_INVALID", "离线依赖目录必须只包含 wheel 文件");
        }
        if (contents.metadata().getOrDefault(
                "requirements-runtime.txt", new byte[0]).length == 0
                || contents.metadata().getOrDefault(
                "requirements-offline.txt", new byte[0]).length == 0) {
            throw failure("REQUIREMENTS_EMPTY", "业务依赖清单不能为空");
        }
        Map<String, String> expected = parseChecksums(
                contents.metadata().get("SHA256SUMS"));
        Map<String, String> actual = new LinkedHashMap<>(contents.files());
        actual.remove("SHA256SUMS");
        if (!expected.equals(actual)) {
            throw failure("CHECKSUM_INVENTORY_INVALID", "包内摘要清单没有精确覆盖全部文件");
        }
    }

    private static VerifiedRelease validateManifest(
            Map<String, String> manifest,
            UUID releaseUid,
            String versionName,
            long sequence,
            String packageSha256,
            long packageSize) {
        requireManifest(manifest, "ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION", "1");
        requireManifest(manifest, "ECOBIN_ARTIFACT_KIND", "orangepi-business-runtime");
        requireManifest(manifest, "ECOBIN_RELEASE_ID", releaseUid.toString());
        requireManifest(manifest, "ECOBIN_VERSION_NAME", versionName);
        requireManifest(manifest, "ECOBIN_RELEASE_SEQUENCE", Long.toString(sequence));
        requireManifest(manifest, "ECOBIN_PYTHON_SERIES", "3.11");
        requireManifest(manifest, "ECOBIN_TARGET_PLATFORM", "linux-arm64");
        requireManifest(manifest, "ECOBIN_EDGE_SCHEMA_VERSION", "40");
        requireManifest(manifest, "ECOBIN_BUSINESS_ALLOWLIST_SHA256", APP_ALLOWLIST_SHA256);
        requireManifest(manifest, "ECOBIN_UART_PROTOCOL_FAMILY", "ECOBIN_UART");
        requireManifest(manifest, "ECOBIN_UART_PROTOCOL_MAJOR", "2");
        requireManifest(manifest, "ECOBIN_UART_PROTOCOL_MINOR", "0");
        requireManifest(manifest, "ECOBIN_UART_REGISTRY_VERSION", "2.0.0-rc.26");
        requireManifest(manifest, "ECOBIN_UART_REGISTRY_SHA256",
                "621feafd1523a6906ec0f16d9d7373c7a2b021a97030bd3d01e3fcfb50e9dfc3");
        requireManifest(manifest, "ECOBIN_ONENET_MAPPING_VERSION", "2.4.0");
        requireManifest(manifest, "ECOBIN_ONENET_MAPPING_SHA256",
                "3e74ad04510ce900c667b32976bf13310687700437a0446fa46739be51662d7f");
        requireManifest(manifest, "ECOBIN_REQUIRED_FIXED_FRAME_REVISION", "NONE");
        requireManifest(manifest,
                "ECOBIN_REQUIRED_MCU_CAPABILITY_BITMAP_HEX",
                "0000000000008100");
        requireManifest(manifest,
                "ECOBIN_PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX",
                "0000000000000000");
        if (!VERSION.matcher(versionName).matches()
                || !GIT_COMMIT.matcher(manifest.get("ECOBIN_GIT_COMMIT")).matches()
                || !digits(manifest.get("ECOBIN_SOURCE_DATE_EPOCH"))) {
            throw failure("MANIFEST_IDENTITY_INVALID", "发布清单中的版本或构建身份不正确");
        }
        int packageFormat = integer(manifest,
                "ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION", 1, 65535);
        int commandContract = integer(manifest,
                "ECOBIN_BACKEND_COMMAND_CONTRACT_VERSION", 1, 65535);
        int eventContract = integer(manifest,
                "ECOBIN_DEVICE_EVENT_CONTRACT_VERSION", 1, 65535);
        int communicationMajor = integer(manifest,
                "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MAJOR", 1, 255);
        int communicationMinor = integer(manifest,
                "ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MINOR", 0, 255);
        int updaterMajor = integer(manifest,
                "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MAJOR", 1, 255);
        int updaterMinor = integer(manifest,
                "ECOBIN_UPDATER_BUSINESS_PROTOCOL_MINOR", 0, 255);
        int uartMajor = integer(manifest,
                "ECOBIN_UART_PROTOCOL_MAJOR", 1, 255);
        int uartMinor = integer(manifest,
                "ECOBIN_UART_PROTOCOL_MINOR", 0, 255);
        String required = manifest.get("ECOBIN_REQUIRED_MCU_CAPABILITY_BITMAP_HEX");
        String provided = manifest.get("ECOBIN_PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX");
        if (!BITMAP.matcher(required).matches() || !BITMAP.matcher(provided).matches()) {
            throw failure("CAPABILITY_BITMAP_INVALID", "发布清单中的能力位格式不正确");
        }
        return new VerifiedRelease(
                releaseUid,
                versionName,
                sequence,
                packageSha256,
                packageSize,
                packageFormat,
                commandContract,
                eventContract,
                communicationMajor,
                communicationMinor,
                updaterMajor,
                updaterMinor,
                "ECOBIN_UART",
                uartMajor,
                uartMinor,
                null,
                required,
                provided,
                Map.copyOf(manifest));
    }

    private static Map<String, String> parseEnv(byte[] bytes, String name) {
        if (bytes == null) {
            throw failure("METADATA_MISSING", name + " 缺失");
        }
        String text = strictUtf8(bytes, name);
        Map<String, String> result = new LinkedHashMap<>();
        for (String line : text.split("\\n", -1)) {
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            int separator = line.indexOf('=');
            if (separator <= 0 || line.indexOf('\r') >= 0) {
                throw failure("METADATA_INVALID", name + " 内容格式不正确");
            }
            String key = line.substring(0, separator);
            String value = line.substring(separator + 1);
            if (value.isEmpty() || result.putIfAbsent(key, value) != null) {
                throw failure("METADATA_INVALID", name + " 包含空值或重复字段");
            }
        }
        return result;
    }

    private static Map<String, String> parseChecksums(byte[] bytes) {
        if (bytes == null) {
            throw failure("CHECKSUM_INVENTORY_MISSING", "包内摘要清单缺失");
        }
        String text = strictUtf8(bytes, "SHA256SUMS");
        Map<String, String> result = new LinkedHashMap<>();
        for (String line : text.split("\\n", -1)) {
            if (line.isEmpty()) {
                continue;
            }
            Matcher matcher = SHA256_LINE.matcher(line);
            if (!matcher.matches()) {
                throw failure("CHECKSUM_INVENTORY_INVALID", "包内摘要清单格式不正确");
            }
            String path = safeRelativeName(matcher.group(2));
            if (path.equals("SHA256SUMS")
                    || result.putIfAbsent(path, matcher.group(1)) != null) {
                throw failure("CHECKSUM_INVENTORY_INVALID", "包内摘要清单包含重复或递归路径");
            }
        }
        if (result.isEmpty()) {
            throw failure("CHECKSUM_INVENTORY_INVALID", "包内摘要清单为空");
        }
        return result;
    }

    private static String safeArchiveName(String name) {
        String candidate = name != null && name.endsWith("/")
                ? name.substring(0, name.length() - 1) : name;
        return safeRelativeName(candidate);
    }

    private static String safeRelativeName(String candidate) {
        if (candidate == null || candidate.isEmpty()
                || candidate.startsWith("/") || candidate.contains("\\")
                || candidate.indexOf('\0') >= 0) {
            throw failure("ARCHIVE_PATH_INVALID", "发布包包含不安全路径");
        }
        String[] parts = candidate.split("/", -1);
        for (String part : parts) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) {
                throw failure("ARCHIVE_PATH_INVALID", "发布包包含不安全路径");
            }
        }
        return candidate;
    }

    private static boolean shouldCapture(String relative) {
        return Set.of(
                "manifest.env",
                "release.env",
                "requirements-runtime.txt",
                "requirements-offline.txt",
                "SHA256SUMS")
                .contains(relative);
    }

    private static String firstPart(String name) {
        int separator = name.indexOf('/');
        return separator < 0 ? name : name.substring(0, separator);
    }

    private static void requireManifest(
            Map<String, String> manifest,
            String key,
            String expected) {
        if (!expected.equals(manifest.get(key))) {
            throw failure("MANIFEST_VALUE_INVALID", "发布清单字段与平台预期不一致：" + key);
        }
    }

    private static int integer(
            Map<String, String> manifest,
            String key,
            int minimum,
            int maximum) {
        try {
            int value = Integer.parseInt(manifest.get(key));
            if (value < minimum || value > maximum) {
                throw new NumberFormatException();
            }
            return value;
        } catch (RuntimeException exception) {
            throw failure("MANIFEST_VALUE_INVALID", "发布清单数值字段不正确：" + key);
        }
    }

    private static boolean digits(String value) {
        return value != null && value.matches("^[0-9]+$");
    }

    private static String digest(Path file) throws IOException {
        MessageDigest digest = sha256();
        try (InputStream input = Files.newInputStream(file)) {
            byte[] buffer = new byte[1024 * 1024];
            int count;
            while ((count = input.read(buffer)) >= 0) {
                if (count > 0) {
                    digest.update(buffer, 0, count);
                }
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (Exception exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String strictUtf8(byte[] bytes, String name) {
        try {
            var decoder = StandardCharsets.UTF_8.newDecoder();
            decoder.onMalformedInput(java.nio.charset.CodingErrorAction.REPORT);
            decoder.onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT);
            return decoder.decode(java.nio.ByteBuffer.wrap(bytes)).toString();
        } catch (Exception exception) {
            throw failure("METADATA_INVALID", name + " 不是有效的 UTF-8 文本");
        }
    }

    private static void requireRegular(Path path, long maximum, String name) {
        try {
            if (!Files.isRegularFile(path, java.nio.file.LinkOption.NOFOLLOW_LINKS)) {
                throw failure("FILE_INVALID", name + "不是普通文件");
            }
            long size = Files.size(path);
            if (size <= 0 || size > maximum) {
                throw failure("FILE_SIZE_INVALID", name + "大小超出允许范围");
            }
        } catch (IOException exception) {
            throw failure("FILE_READ_FAILED", name + "不可读");
        }
    }

    private static VerificationException failure(String code, String message) {
        return new VerificationException(code, message);
    }

    private static String safeMessage(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.isBlank()
                ? exception.getClass().getSimpleName() : message;
    }

    private record ArchiveContents(
            Set<String> directories,
            Map<String, String> files,
            Map<String, byte[]> metadata) {
    }

    public record VerifiedRelease(
            UUID releaseUid,
            String versionName,
            long releaseSequence,
            String packageSha256,
            long packageSize,
            int packageFormatVersion,
            int backendCommandContractVersion,
            int deviceEventContractVersion,
            int communicationBusinessProtocolMajor,
            int communicationBusinessProtocolMinor,
            int updaterBusinessProtocolMajor,
            int updaterBusinessProtocolMinor,
            String uartProtocolFamily,
            Integer uartProtocolMajor,
            Integer uartProtocolMinor,
            Integer requiredFixedFrameRevision,
            String requiredMcuCapabilityBitmapHex,
            String providedBusinessCapabilityBitmapHex,
            Map<String, String> manifest) {
    }

    public static final class VerificationException extends RuntimeException {
        private final String code;

        VerificationException(String code, String message) {
            super(message);
            this.code = code;
        }

        public String code() {
            return code;
        }
    }
}
