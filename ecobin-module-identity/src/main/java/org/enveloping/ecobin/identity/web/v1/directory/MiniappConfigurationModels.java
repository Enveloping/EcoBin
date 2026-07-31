package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

import java.time.Instant;

public final class MiniappConfigurationModels {

    private MiniappConfigurationModels() {
    }

    public record PutMiniappConfigurationRequest(
            @NotBlank
            @Size(max = 32)
            @Pattern(regexp = "^wx[0-9A-Za-z]{16}$")
            String appId,
            @NotBlank @Size(max = 100) String displayName,
            @Size(min = 1, max = 256) String appSecret,
            @PositiveOrZero Long expectedVersion) {
    }

    public record MiniappConfigurationView(
            String appId,
            String displayName,
            String appSecret,
            boolean appSecretConfigured,
            String maskedAppSecret,
            boolean activated,
            boolean loginEnabled,
            long version,
            Instant configuredAt,
            Instant activatedAt,
            Instant updatedAt) {
    }

    public record MiniappConfigurationMutationView(
            String appId,
            String displayName,
            boolean appSecretConfigured,
            String maskedAppSecret,
            boolean activated,
            boolean loginEnabled,
            long version,
            Instant configuredAt,
            Instant activatedAt,
            Instant updatedAt) {
    }
}
