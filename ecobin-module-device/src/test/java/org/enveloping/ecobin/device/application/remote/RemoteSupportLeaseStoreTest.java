package org.enveloping.ecobin.device.application.remote;

import org.enveloping.ecobin.device.application.enrollment.RemoteSupportBootstrapProperties;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.ObjectMapper;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RemoteSupportLeaseStoreTest {

    @TempDir
    Path temporaryDirectory;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private Path desiredDirectory;
    private Path actualDirectory;
    private RemoteSupportLeaseStore store;

    @BeforeEach
    void setUp() throws Exception {
        desiredDirectory = Files.createDirectory(
                temporaryDirectory.resolve("desired"));
        actualDirectory = Files.createDirectory(
                temporaryDirectory.resolve("actual"));
        RemoteSupportBootstrapProperties properties =
                new RemoteSupportBootstrapProperties();
        properties.setEnabled(true);
        properties.setLeaseDesiredDirectory(
                desiredDirectory.toString());
        properties.setLeaseActualDirectory(
                actualDirectory.toString());
        store = new RemoteSupportLeaseStore(objectMapper, properties);
    }

    @Test
    void delayedOldSessionRevokeCannotDeleteReusedPortLease() {
        RemoteSupportLeaseStore.Lease current = lease(UUID.randomUUID());
        store.publish(current);

        assertThatThrownBy(() -> store.revoke(
                UUID.randomUUID(), current.port()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("another remote support session");
        assertThat(desiredDirectory.resolve("22011.json")).exists();

        store.revoke(current.sessionUid(), current.port());

        assertThat(desiredDirectory.resolve("22011.json"))
                .doesNotExist();
    }

    @Test
    void actualLeaseInspectionDistinguishesAbsentMatchAndConflict()
            throws Exception {
        RemoteSupportLeaseStore.Lease lease = lease(UUID.randomUUID());
        assertThat(inspect(lease)).isEqualTo(
                RemoteSupportReconciliationPolicy.ActualLeaseState.ABSENT);

        Map<String, Object> marker = new LinkedHashMap<>();
        marker.put("schemaVersion", 1);
        marker.put("sessionUid", lease.sessionUid().toString());
        marker.put("hardwareSn", lease.hardwareSn());
        marker.put("keyFingerprint", lease.keyFingerprint());
        marker.put("listenHost", "127.0.0.1");
        marker.put("listenPort", lease.port());
        marker.put("connectedAtEpochSecond", 1_786_750_000L);
        marker.put("expiresAtEpochSecond",
                lease.expiresAt().getEpochSecond());
        marker.put("guardPid", 1234);
        Files.write(
                actualDirectory.resolve("22011.json"),
                objectMapper.writeValueAsBytes(marker));

        assertThat(inspect(lease)).isEqualTo(
                RemoteSupportReconciliationPolicy.ActualLeaseState.MATCH);

        marker.put("sessionUid", UUID.randomUUID().toString());
        Files.write(
                actualDirectory.resolve("22011.json"),
                objectMapper.writeValueAsBytes(marker));
        assertThat(inspect(lease)).isEqualTo(
                RemoteSupportReconciliationPolicy.ActualLeaseState.CONFLICT);
    }

    private RemoteSupportReconciliationPolicy.ActualLeaseState inspect(
            RemoteSupportLeaseStore.Lease lease) {
        return store.inspectActual(
                lease.sessionUid(),
                lease.hardwareSn(),
                lease.port(),
                lease.keyFingerprint(),
                lease.expiresAt());
    }

    private static RemoteSupportLeaseStore.Lease lease(UUID sessionUid) {
        Instant createdAt = Instant.parse("2026-08-15T12:00:00Z");
        return new RemoteSupportLeaseStore.Lease(
                sessionUid,
                "SN-REMOTE-0001",
                22011,
                "ssh-ed25519",
                "A".repeat(68),
                "SHA256:" + "B".repeat(43),
                createdAt,
                createdAt.plusSeconds(900));
    }
}
