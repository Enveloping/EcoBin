package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.DeviceEnrollmentCrypto;
import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermission;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.TimeUnit;

/** Invokes OpenSSH directly without a shell; operator private keys never enter. */
@Component
public class OpenSshMaintenanceCertificateSigner {

    private static final DateTimeFormatter OPENSSH_TIME =
            DateTimeFormatter.ofPattern("yyyyMMddHHmmss'Z'")
                    .withZone(ZoneOffset.UTC);
    private static final Set<PosixFilePermission> DIRECTORY_PERMISSIONS =
            Set.of(
                    PosixFilePermission.OWNER_READ,
                    PosixFilePermission.OWNER_WRITE,
                    PosixFilePermission.OWNER_EXECUTE);

    private final RemoteSupportBootstrapProperties properties;

    public OpenSshMaintenanceCertificateSigner(
            RemoteSupportBootstrapProperties properties) {
        this.properties = properties;
    }

    public String sign(
            long serial,
            UUID platformAdminUid,
            UUID sessionUid,
            String hardwareSn,
            String operatorPublicKey,
            Instant validAfter,
            Instant validBefore) {
        if (!properties.isEnabled()) {
            throw new IllegalStateException("remote support is disabled");
        }
        if (serial < 1 || validAfter == null || validBefore == null
                || !validBefore.isAfter(validAfter)
                || Duration.between(validAfter, validBefore)
                        .compareTo(Duration.ofMinutes(30)) > 0) {
            throw new IllegalArgumentException(
                    "invalid maintenance certificate validity");
        }
        DeviceEnrollmentCrypto.ed25519RawPublicKey(operatorPublicKey);
        Path ca = requirePrivateCa();
        Path temporaryDirectory = null;
        try {
            temporaryDirectory = Files.createTempDirectory(
                    "ecobin-maintenance-certificate-");
            setPosixPermissionsIfSupported(
                    temporaryDirectory, DIRECTORY_PERMISSIONS);
            Path publicKey = temporaryDirectory.resolve("operator.pub");
            Files.writeString(
                    publicKey,
                    operatorPublicKey + "\n",
                    StandardCharsets.US_ASCII);
            String keyId = "platform-admin-" + platformAdminUid
                    + ":session-" + sessionUid;
            Process process = new ProcessBuilder(
                    "ssh-keygen",
                    "-q",
                    "-s", ca.toString(),
                    "-I", keyId,
                    "-z", Long.toUnsignedString(serial),
                    "-n", "ecobin-jump,ecobin-device-" + hardwareSn,
                    "-V", OPENSSH_TIME.format(validAfter)
                            + ":" + OPENSSH_TIME.format(validBefore),
                    "-O", "clear",
                    "-O", "permit-port-forwarding",
                    "-O", "permit-pty",
                    publicKey.toString())
                    .redirectErrorStream(true)
                    .start();
            boolean finished = process.waitFor(10, TimeUnit.SECONDS);
            if (!finished) {
                process.destroyForcibly();
                throw new IllegalStateException(
                        "maintenance certificate signer timed out");
            }
            byte[] output = process.getInputStream().readNBytes(16_385);
            if (output.length > 16_384 || process.exitValue() != 0) {
                throw new IllegalStateException(
                        "maintenance certificate signer rejected the request");
            }
            Path certificate = temporaryDirectory.resolve(
                    "operator-cert.pub");
            if (!Files.isRegularFile(
                    certificate, LinkOption.NOFOLLOW_LINKS)
                    || Files.size(certificate) > 2048) {
                throw new IllegalStateException(
                        "maintenance certificate output is invalid");
            }
            String value = Files.readString(
                    certificate, StandardCharsets.US_ASCII).trim();
            if (!value.matches(
                    "^ssh-ed25519-cert-v01@openssh\\.com [A-Za-z0-9+/=]+(?: [^\\r\\n]{1,200})?$")) {
                throw new IllegalStateException(
                        "maintenance certificate output is not canonical");
            }
            return value;
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "maintenance certificate signer is unavailable",
                    failure);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(
                    "maintenance certificate signing was interrupted",
                    interrupted);
        } finally {
            deleteTemporaryCertificateDirectory(temporaryDirectory);
        }
    }

    private Path requirePrivateCa() {
        String configured = properties.getSignerCaPrivateKeyPath();
        if (configured == null || configured.isBlank()) {
            throw new IllegalStateException(
                    "maintenance CA private key path is not configured");
        }
        Path path = Path.of(configured).toAbsolutePath().normalize();
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException(
                    "maintenance CA private key is unavailable");
        }
        try {
            if (Files.getFileStore(path).supportsFileAttributeView("posix")) {
                Set<PosixFilePermission> permissions =
                        Files.getPosixFilePermissions(path);
                requireSafePrivateCaPermissions(permissions);
            }
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "maintenance CA private key permissions cannot be verified",
                    failure);
        }
        return path;
    }

    static void requireSafePrivateCaPermissions(
            Set<PosixFilePermission> permissions) {
        if (!permissions.contains(PosixFilePermission.OWNER_READ)
                || permissions.contains(PosixFilePermission.GROUP_WRITE)
                || permissions.contains(PosixFilePermission.GROUP_EXECUTE)
                || permissions.contains(PosixFilePermission.OTHERS_READ)
                || permissions.contains(PosixFilePermission.OTHERS_WRITE)
                || permissions.contains(PosixFilePermission.OTHERS_EXECUTE)) {
            throw new IllegalStateException(
                    "maintenance CA private key permissions are too broad");
        }
    }

    private static void setPosixPermissionsIfSupported(
            Path path,
            Set<PosixFilePermission> permissions) throws IOException {
        if (Files.getFileStore(path).supportsFileAttributeView("posix")) {
            Files.setPosixFilePermissions(path, permissions);
        }
    }

    private static void deleteTemporaryCertificateDirectory(Path directory) {
        if (directory == null
                || !directory.getFileName().toString()
                        .startsWith("ecobin-maintenance-certificate-")) {
            return;
        }
        try {
            Files.deleteIfExists(directory.resolve("operator-cert.pub"));
            Files.deleteIfExists(directory.resolve("operator.pub"));
            Files.deleteIfExists(directory);
        } catch (IOException ignored) {
            // Temp cleanup failure contains no private material; only the
            // operator public key and public certificate were written.
        }
    }
}
