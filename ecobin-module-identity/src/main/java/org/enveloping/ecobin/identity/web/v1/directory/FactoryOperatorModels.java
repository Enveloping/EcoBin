package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.UUID;

public final class FactoryOperatorModels {

    private FactoryOperatorModels() {
    }

    public record FactoryOperatorView(
            UUID factoryOperatorUid,
            String operatorCode,
            String displayName,
            String status,
            long version,
            long authVersion,
            String bindingStatus,
            UUID bindingUid,
            Instant boundAt,
            Instant createdAt,
            Instant updatedAt) {
    }

    public record CreateFactoryOperatorRequest(
            @NotBlank
            @Size(min = 2, max = 64)
            @Pattern(regexp = "^[A-Za-z0-9][A-Za-z0-9_-]*$")
            String operatorCode,
            @NotBlank @Size(max = 100) String displayName) {
    }

    public record UpdateFactoryOperatorRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotBlank @Size(max = 100) String displayName) {
    }

    public record FactoryOperatorStatusRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record FactoryBindingRevocationRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record SetFactoryOperatorMiniappBindingRequest(
            @NotNull @PositiveOrZero Long expectedVersion,
            @NotBlank
            @Size(max = 32)
            @Pattern(regexp = "^[a-z0-9][a-z0-9-]*$")
            String tenantCode,
            @NotBlank
            @Size(max = 32)
            @Pattern(regexp = "^[a-z0-9][a-z0-9-]*$")
            String organizationCode,
            @NotNull UUID organizationUserUid,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record FactoryBindingIntentCreated(
            UUID bindingIntentUid,
            Instant expiresAt,
            String miniProgramCodeDataUrl) {
    }
}
