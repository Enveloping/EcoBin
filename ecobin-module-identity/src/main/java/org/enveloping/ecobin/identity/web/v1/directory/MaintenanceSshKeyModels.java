package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.UUID;

public final class MaintenanceSshKeyModels {

    private MaintenanceSshKeyModels() {
    }

    public record CreateMaintenanceSshKeyRequest(
            @NotBlank @Size(max = 100) String label,
            @NotBlank @Size(max = 128) String publicKey) {
    }

    public record RevokeMaintenanceSshKeyRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record MaintenanceSshKeyView(
            UUID maintenanceSshKeyUid,
            String label,
            String publicKey,
            String fingerprintSha256,
            String status,
            long version,
            Instant createdAt,
            Instant updatedAt,
            Instant revokedAt,
            String revokedReason) {
    }
}
