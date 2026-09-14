package org.enveloping.ecobin.device.application.software;

import org.apache.commons.compress.archivers.tar.TarArchiveEntry;
import org.apache.commons.compress.archivers.tar.TarArchiveOutputStream;
import org.apache.commons.compress.compressors.gzip.GzipCompressorOutputStream;
import org.enveloping.ecobin.device.api.port.BusinessReleaseSigningKeyPort;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.MessageDigest;
import java.security.Signature;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BusinessReleasePackageVerifierTest {

    private static final UUID RELEASE_UID = UUID.fromString(
            "11111111-1111-4111-8111-111111111111");
    private static final Pattern PYTHON_STRING = Pattern.compile(
            "\\\"([^\\\"]+)\\\"");
    private static final List<String> APP_FILES =
            canonicalBusinessAppFiles();

    @TempDir
    Path temporary;

    @Test
    void verifiesTheSignedArchiveAndCompatibilityDeclaration() throws Exception {
        KeyPair keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path archive = buildArchive(false);
        Path signature = sign(archive, keys);
        BusinessReleasePackageVerifier verifier = verifier(keys);

        var result = verifier.verify(
                archive, signature, "business_2026",
                RELEASE_UID, "1.1.0", 2);

        assertThat(result.releaseUid()).isEqualTo(RELEASE_UID);
        assertThat(result.packageFormatVersion()).isEqualTo(1);
        assertThat(result.backendCommandContractVersion()).isEqualTo(2);
        assertThat(result.communicationBusinessProtocolMajor()).isEqualTo(1);
        assertThat(result.uartProtocolFamily()).isEqualTo("ECOBIN_UART");
        assertThat(result.uartProtocolMajor()).isEqualTo(2);
        assertThat(result.uartProtocolMinor()).isZero();
        assertThat(result.requiredFixedFrameRevision()).isNull();
    }

    @Test
    void rejectsAChangedSignatureBeforeTrustingArchiveContents() throws Exception {
        KeyPair keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path archive = buildArchive(false);
        Path signature = sign(archive, keys);
        byte[] changed = Files.readAllBytes(signature);
        changed[0] ^= 1;
        Files.write(signature, changed);

        assertThatThrownBy(() -> verifier(keys).verify(
                archive, signature, "business_2026",
                RELEASE_UID, "1.1.0", 2))
                .isInstanceOf(
                        BusinessReleasePackageVerifier.VerificationException.class)
                .hasMessageContaining("签名验证失败");
    }

    @Test
    void rejectsSignedPackagesThatCrossTheBusinessCodeBoundary() throws Exception {
        KeyPair keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path archive = buildArchive(true);
        Path signature = sign(archive, keys);

        assertThatThrownBy(() -> verifier(keys).verify(
                archive, signature, "business_2026",
                RELEASE_UID, "1.1.0", 2))
                .isInstanceOf(
                        BusinessReleasePackageVerifier.VerificationException.class)
                .hasMessageContaining("业务代码文件不符合固定白名单");
    }

    @Test
    void rejectsSignedPackagesMissingSharedWeightValidation() throws Exception {
        KeyPair keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path archive = buildArchive(false, false, true);
        Path signature = sign(archive, keys);

        assertThatThrownBy(() -> verifier(keys).verify(
                archive, signature, "business_2026",
                RELEASE_UID, "1.1.0", 2))
                .isInstanceOf(
                        BusinessReleasePackageVerifier.VerificationException.class)
                .hasMessageContaining("业务代码文件不符合固定白名单");
    }

    @Test
    void rejectsSignedPackagesWithAnUnexpectedEmptyDirectory() throws Exception {
        KeyPair keys = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
        Path archive = buildArchive(false, true);
        Path signature = sign(archive, keys);

        assertThatThrownBy(() -> verifier(keys).verify(
                archive, signature, "business_2026",
                RELEASE_UID, "1.1.0", 2))
                .isInstanceOf(
                        BusinessReleasePackageVerifier.VerificationException.class)
                .hasMessageContaining("包含额外目录");
    }

    private BusinessReleasePackageVerifier verifier(KeyPair keys) {
        byte[] pem = ("-----BEGIN PUBLIC KEY-----\n"
                + Base64.getMimeEncoder(64, new byte[]{'\n'})
                .encodeToString(keys.getPublic().getEncoded())
                + "\n-----END PUBLIC KEY-----\n")
                .getBytes(StandardCharsets.US_ASCII);
        BusinessReleaseSigningKeyPort port = new BusinessReleaseSigningKeyPort() {
            @Override
            public Readiness readiness() {
                return new Readiness(true, "available");
            }

            @Override
            public byte[] loadEd25519PublicKey(String signingKeyId) {
                return pem.clone();
            }
        };
        return new BusinessReleasePackageVerifier(port);
    }

    private Path buildArchive(boolean addPermanentFile) throws Exception {
        return buildArchive(addPermanentFile, false);
    }

    private Path buildArchive(
            boolean addPermanentFile,
            boolean addUnexpectedDirectory) throws Exception {
        return buildArchive(addPermanentFile, addUnexpectedDirectory, false);
    }

    private Path buildArchive(
            boolean addPermanentFile,
            boolean addUnexpectedDirectory,
            boolean omitWeightValidation) throws Exception {
        Map<String, byte[]> files = new LinkedHashMap<>();
        for (String app : APP_FILES) {
            if (omitWeightValidation
                    && "factory_seal/weight_validation.py".equals(app)) {
                continue;
            }
            files.put("app/" + app,
                    ("# " + app + "\n").getBytes(StandardCharsets.UTF_8));
        }
        if (addPermanentFile) {
            files.put("app/communication_agent.py", "# forbidden\n"
                    .getBytes(StandardCharsets.UTF_8));
        }
        files.put("wheelhouse/demo-1.0-py3-none-any.whl", new byte[]{1, 2, 3});
        files.put("requirements-runtime.txt", "demo==1.0\n"
                .getBytes(StandardCharsets.UTF_8));
        files.put("requirements-offline.txt",
                "demo==1.0 --hash=sha256:abc\n".getBytes(StandardCharsets.UTF_8));
        files.put("manifest.env", manifest().getBytes(StandardCharsets.UTF_8));
        files.put("release.env", ("ECOBIN_EDGE_VERSION=1.1.0\n"
                + "ECOBIN_BUSINESS_RELEASE_ID=" + RELEASE_UID + "\n"
                + "ECOBIN_BUSINESS_RELEASE_SEQUENCE=2\n")
                .getBytes(StandardCharsets.UTF_8));
        StringBuilder checksums = new StringBuilder();
        files.entrySet().stream()
                .sorted(Map.Entry.comparingByKey())
                .forEach(entry -> checksums.append(hex(entry.getValue()))
                        .append("  ").append(entry.getKey()).append('\n'));
        files.put("SHA256SUMS", checksums.toString()
                .getBytes(StandardCharsets.UTF_8));

        Path archive = temporary.resolve(addPermanentFile
                ? "forbidden.tar.gz"
                : addUnexpectedDirectory
                ? "unexpected-directory.tar.gz"
                : "package.tar.gz");
        String root = "ecobin-business-" + RELEASE_UID;
        try (OutputStream file = Files.newOutputStream(archive);
             GzipCompressorOutputStream gzip = new GzipCompressorOutputStream(file);
             TarArchiveOutputStream tar = new TarArchiveOutputStream(gzip)) {
            List<String> directories = new ArrayList<>(List.of(
                    "", "app", "app/factory_seal", "app/system",
                    "wheelhouse", "migrations"));
            if (addUnexpectedDirectory) {
                directories.add("app/unused");
            }
            for (String directory : directories) {
                String name = directory.isEmpty() ? root : root + "/" + directory;
                TarArchiveEntry entry = new TarArchiveEntry(name + "/");
                entry.setMode(0755);
                tar.putArchiveEntry(entry);
                tar.closeArchiveEntry();
            }
            files.entrySet().stream()
                    .sorted(Comparator.comparing(Map.Entry::getKey))
                    .forEach(entry -> writeEntry(tar, root, entry));
            tar.finish();
        }
        return archive;
    }

    private static void writeEntry(
            TarArchiveOutputStream tar,
            String root,
            Map.Entry<String, byte[]> source) {
        try {
            TarArchiveEntry entry = new TarArchiveEntry(
                    root + "/" + source.getKey());
            entry.setSize(source.getValue().length);
            entry.setMode(0644);
            tar.putArchiveEntry(entry);
            tar.write(source.getValue());
            tar.closeArchiveEntry();
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private Path sign(Path archive, KeyPair keys) throws Exception {
        Signature signer = Signature.getInstance("Ed25519");
        signer.initSign(keys.getPrivate());
        signer.update(Files.readAllBytes(archive));
        Path signature = temporary.resolve(archive.getFileName() + ".sig");
        Files.write(signature, signer.sign());
        return signature;
    }

    private static String manifest() {
        return ("""
                ECOBIN_BUSINESS_RELEASE_FORMAT_VERSION=1
                ECOBIN_ARTIFACT_KIND=orangepi-business-runtime
                ECOBIN_RELEASE_ID=11111111-1111-4111-8111-111111111111
                ECOBIN_VERSION_NAME=1.1.0
                ECOBIN_RELEASE_SEQUENCE=2
                ECOBIN_GIT_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                ECOBIN_PYTHON_SERIES=3.11
                ECOBIN_TARGET_PLATFORM=linux-arm64
                ECOBIN_EDGE_SCHEMA_VERSION=40
                ECOBIN_SOURCE_DATE_EPOCH=1
                ECOBIN_BUSINESS_ALLOWLIST_SHA256=%s
                ECOBIN_BACKEND_COMMAND_CONTRACT_VERSION=2
                ECOBIN_DEVICE_EVENT_CONTRACT_VERSION=2
                ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MAJOR=1
                ECOBIN_COMMUNICATION_BUSINESS_PROTOCOL_MINOR=0
                ECOBIN_UPDATER_BUSINESS_PROTOCOL_MAJOR=1
                ECOBIN_UPDATER_BUSINESS_PROTOCOL_MINOR=0
                ECOBIN_UART_PROTOCOL_FAMILY=ECOBIN_UART
                ECOBIN_UART_PROTOCOL_MAJOR=2
                ECOBIN_UART_PROTOCOL_MINOR=0
                ECOBIN_UART_REGISTRY_VERSION=2.0.0-rc.26
                ECOBIN_UART_REGISTRY_SHA256=621feafd1523a6906ec0f16d9d7373c7a2b021a97030bd3d01e3fcfb50e9dfc3
                ECOBIN_ONENET_MAPPING_VERSION=2.4.0
                ECOBIN_ONENET_MAPPING_SHA256=3e74ad04510ce900c667b32976bf13310687700437a0446fa46739be51662d7f
                ECOBIN_REQUIRED_FIXED_FRAME_REVISION=NONE
                ECOBIN_REQUIRED_MCU_CAPABILITY_BITMAP_HEX=0000000000008100
                ECOBIN_PROVIDED_BUSINESS_CAPABILITY_BITMAP_HEX=0000000000000000
                """).formatted(canonicalBusinessAllowlistSha256());
    }

    private static List<String> canonicalBusinessAppFiles() {
        try {
            String source = Files.readString(canonicalManifestPath());
            List<String> runtime = pythonStrings(section(
                    source, "RUNTIME_APP_FILES = (", "\n)"));
            Set<String> excluded = new LinkedHashSet<>(pythonStrings(section(
                    source, "    not in {", "\n    }")));
            List<String> result = runtime.stream()
                    .filter(name -> !excluded.contains(name))
                    .toList();
            if (result.isEmpty() || !result.contains("camera_selection.py")) {
                throw new IllegalStateException(
                        "canonical business runtime inventory is incomplete");
            }
            return result;
        } catch (Exception exception) {
            throw new IllegalStateException(
                    "cannot read canonical hardware business inventory",
                    exception);
        }
    }

    private static Path canonicalManifestPath() {
        for (Path candidate : List.of(
                Path.of("hardware", "install", "runtime_payload_manifest.py"),
                Path.of("..", "hardware", "install",
                        "runtime_payload_manifest.py"))) {
            Path normalized = candidate.toAbsolutePath().normalize();
            if (Files.isRegularFile(normalized)) {
                return normalized;
            }
        }
        throw new IllegalStateException(
                "hardware/install/runtime_payload_manifest.py is missing");
    }

    private static String section(String source, String start, String end) {
        int from = source.indexOf(start);
        if (from < 0) {
            throw new IllegalStateException("canonical inventory start is missing");
        }
        int to = source.indexOf(end, from + start.length());
        if (to < 0) {
            throw new IllegalStateException("canonical inventory end is missing");
        }
        return source.substring(from + start.length(), to);
    }

    private static List<String> pythonStrings(String section) {
        List<String> values = new ArrayList<>();
        Matcher matcher = PYTHON_STRING.matcher(section);
        while (matcher.find()) {
            values.add(matcher.group(1));
        }
        return values;
    }

    private static String canonicalBusinessAllowlistSha256() {
        StringBuilder payload = new StringBuilder();
        APP_FILES.forEach(name -> payload.append(name).append('\n'));
        return hex(payload.toString().getBytes(StandardCharsets.UTF_8));
    }

    private static String hex(byte[] value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }
}
