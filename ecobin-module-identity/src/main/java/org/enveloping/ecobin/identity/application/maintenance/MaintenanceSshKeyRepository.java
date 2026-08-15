package org.enveloping.ecobin.identity.application.maintenance;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

interface MaintenanceSshKeyRepository {

    Optional<PlatformAdminRow> findPlatformAdmin(
            long platformAdminId,
            UUID platformAdminUid,
            boolean forUpdate);

    List<MaintenanceSshKeyRow> findAll(long platformAdminId);

    Optional<MaintenanceSshKeyRow> findByUid(
            long platformAdminId,
            UUID maintenanceSshKeyUid,
            boolean forUpdate);

    Optional<MaintenanceSshKeyRow> findByFingerprint(
            long platformAdminId,
            String fingerprintSha256);

    Optional<MaintenanceSshKeyRow> findActive(
            UUID platformAdminUid,
            UUID maintenanceSshKeyUid,
            boolean forUpdate);

    void insert(
            UUID maintenanceSshKeyUid,
            long platformAdminId,
            String label,
            String publicKey,
            String fingerprintSha256);

    int revoke(
            long keyId,
            long expectedVersion,
            String reason);

    record PlatformAdminRow(
            long id,
            UUID uid,
            boolean enabled,
            long authVersion,
            Instant deletedAt) {
    }

    record MaintenanceSshKeyRow(
            long id,
            UUID uid,
            long platformAdminId,
            String label,
            String publicKey,
            String fingerprintSha256,
            Instant revokedAt,
            String revokedReason,
            long version,
            Instant createdAt,
            Instant updatedAt) {
    }
}
