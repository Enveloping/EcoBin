package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.UUID;

public final class PlatformAdminModels {

    private PlatformAdminModels() {
    }

    public record PlatformAdminView(
            UUID platformAdminUid,
            String adminKind,
            String loginName,
            String displayName,
            String status,
            long version,
            long authVersion,
            Instant createdAt,
            Instant updatedAt,
            Instant deletedAt) {
    }

    public record CreatePlatformAdminRequest(
            @NotBlank
            @Size(max = 64)
            @Pattern(regexp = "^[A-Za-z0-9][A-Za-z0-9._-]*$")
            String loginName,
            @NotBlank @Size(min = 8, max = 256) String initialPassword,
            @NotBlank @Size(max = 100) String displayName) {
    }

    public record DeletePlatformAdminRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotNull @PositiveOrZero Long expectedAuthVersion,
            @NotBlank @Size(max = 500) String reason) {
    }
}
