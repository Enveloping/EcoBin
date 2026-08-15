package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.DeviceEnrollmentCrypto;
import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermission;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Atomic adapter for the jump server's desired/actual lease protocol. */
@Component
public class RemoteSupportLeaseStore {

    private static final Set<Integer> PORTS = Set.of(
            22011, 22012, 22013, 22014);
    private static final Set<PosixFilePermission> FILE_PERMISSIONS = Set.of(
            PosixFilePermission.OWNER_READ,
            PosixFilePermission.OWNER_WRITE,
            PosixFilePermission.GROUP_READ);

    private final ObjectMapper objectMapper;
    private final RemoteSupportBootstrapProperties properties;

    public RemoteSupportLeaseStore(
            ObjectMapper objectMapper,
            RemoteSupportBootstrapProperties properties) {
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    public void publish(Lease lease) {
        requireEnabled();
        Path directory = requireDirectory(
                properties.getLeaseDesiredDirectory(), true);
        requirePort(lease.port());
        if (!"ssh-ed25519".equals(lease.keyType())
                || lease.expiresAt().isAfter(
                        lease.createdAt().plusSeconds(1800))
                || !lease.expiresAt().isAfter(lease.createdAt())) {
            throw new IllegalArgumentException("invalid remote support lease");
        }
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("schemaVersion", 1);
        value.put("sessionUid", lease.sessionUid().toString());
        value.put("hardwareSn", lease.hardwareSn());
        value.put("keyType", lease.keyType());
        value.put("keyBase64", lease.keyBase64());
        value.put("keyFingerprint", lease.keyFingerprint());
        value.put("listenHost", "127.0.0.1");
        value.put("listenPort", lease.port());
        value.put("createdAtEpochSecond", lease.createdAt().getEpochSecond());
        value.put("expiresAtEpochSecond", lease.expiresAt().getEpochSecond());
        byte[] json = objectMapper.writeValueAsBytes(value);
        Path target = directory.resolve(lease.port() + ".json");
        Path temporary = directory.resolve(
                "." + lease.port() + "." + UUID.randomUUID() + ".tmp");
        try {
            Files.createFile(temporary);
            setPosixPermissionsIfSupported(temporary);
            try (FileChannel channel = FileChannel.open(
                    temporary,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.TRUNCATE_EXISTING)) {
                ByteBuffer buffer = ByteBuffer.wrap(json);
                while (buffer.hasRemaining()) {
                    channel.write(buffer);
                }
                channel.force(true);
            }
            try {
                Files.move(
                        temporary,
                        target,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException unsupported) {
                throw new IllegalStateException(
                        "desired lease filesystem lacks atomic rename",
                        unsupported);
            }
            forceDirectory(directory);
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "remote support desired lease cannot be published",
                    failure);
        } finally {
            try {
                Files.deleteIfExists(temporary);
            } catch (IOException ignored) {
                // The authoritative target was already atomically published;
                // a hidden temp cleanup failure is handled operationally.
            }
        }
    }

    public void revoke(UUID sessionUid, int port) {
        requireEnabled();
        requirePort(port);
        Path directory = requireDirectory(
                properties.getLeaseDesiredDirectory(), true);
        Path target = directory.resolve(port + ".json");
        if (!Files.exists(target, LinkOption.NOFOLLOW_LINKS)) {
            return;
        }
        JsonNode value = readSmallRegularFile(target);
        if (!sessionUid.toString().equals(
                value.path("sessionUid").asString())) {
            throw new IllegalStateException(
                    "refusing to revoke another remote support session");
        }
        try {
            Files.delete(target);
            forceDirectory(directory);
        } catch (IOException failure) {
            throw new IllegalStateException(
                    "remote support desired lease cannot be revoked",
                    failure);
        }
    }

    public boolean actualMatches(
            UUID sessionUid,
            String hardwareSn,
            int port,
            String expectedFingerprint,
            Instant expiresAt) {
        if (!properties.isEnabled()) {
            return false;
        }
        requirePort(port);
        Path directory = requireDirectory(
                properties.getLeaseActualDirectory(), false);
        Path marker = directory.resolve(port + ".json");
        if (!Files.exists(marker, LinkOption.NOFOLLOW_LINKS)) {
            return false;
        }
        try {
            JsonNode value = readSmallRegularFile(marker);
            return value.size() == 9
                    && value.path("schemaVersion").asInt() == 1
                    && sessionUid.toString().equals(
                            value.path("sessionUid").asString())
                    && hardwareSn.equals(
                            value.path("hardwareSn").asString())
                    && expectedFingerprint.equals(
                            value.path("keyFingerprint").asString())
                    && "127.0.0.1".equals(
                            value.path("listenHost").asString())
                    && value.path("listenPort").asInt() == port
                    && value.path("expiresAtEpochSecond").longValue()
                            == expiresAt.getEpochSecond()
                    && value.path("guardPid").longValue() > 0;
        } catch (RuntimeException invalidMarker) {
            return false;
        }
    }

    public static KeyParts keyParts(String canonicalPublicKey) {
        byte[] fingerprint = DeviceEnrollmentCrypto.sshFingerprint(
                canonicalPublicKey);
        return new KeyParts(
                "ssh-ed25519",
                canonicalPublicKey.substring("ssh-ed25519 ".length()),
                "SHA256:" + Base64.getEncoder().withoutPadding()
                        .encodeToString(fingerprint));
    }

    private JsonNode readSmallRegularFile(Path path) {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException("lease path is not a regular file");
        }
        try {
            long size = Files.size(path);
            if (size < 2 || size > 4096) {
                throw new IllegalStateException(
                        "lease file has an invalid size");
            }
            return objectMapper.readTree(Files.readAllBytes(path));
        } catch (IOException failure) {
            throw new IllegalStateException("lease file cannot be read", failure);
        }
    }

    private void requireEnabled() {
        if (!properties.isEnabled()) {
            throw new IllegalStateException("remote support is disabled");
        }
    }

    private static Path requireDirectory(
            String configured,
            boolean writable) {
        if (configured == null || configured.isBlank()) {
            throw new IllegalStateException(
                    "remote support lease directory is not configured");
        }
        Path path = Path.of(configured).toAbsolutePath().normalize();
        if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)
                || (writable && !Files.isWritable(path))) {
            throw new IllegalStateException(
                    "remote support lease directory is unavailable");
        }
        return path;
    }

    private static void requirePort(int port) {
        if (!PORTS.contains(port)) {
            throw new IllegalArgumentException(
                    "remote support port is outside the fixed pool");
        }
    }

    private static void setPosixPermissionsIfSupported(Path path)
            throws IOException {
        if (Files.getFileStore(path).supportsFileAttributeView("posix")) {
            Files.setPosixFilePermissions(path, FILE_PERMISSIONS);
        }
    }

    private static void forceDirectory(Path directory) throws IOException {
        try (FileChannel channel = FileChannel.open(
                directory, StandardOpenOption.READ)) {
            channel.force(true);
        }
    }

    public record Lease(
            UUID sessionUid,
            String hardwareSn,
            int port,
            String keyType,
            String keyBase64,
            String keyFingerprint,
            Instant createdAt,
            Instant expiresAt) {
    }

    public record KeyParts(
            String keyType,
            String keyBase64,
            String fingerprint) {
    }
}
