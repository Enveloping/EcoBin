package org.enveloping.ecobin.device.web.v1.enrollment;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import tools.jackson.databind.JsonNode;

import java.time.Instant;
import java.util.UUID;

public final class DeviceEnrollmentModels {

    private DeviceEnrollmentModels() {
    }

    public record ChallengeView(
            int schemaVersion,
            UUID challengeUid,
            String enrollmentKeyId,
            String nonce,
            Instant expiresAt) {
    }

    public record EnrollmentRequest(
            @NotNull Integer schemaVersion,
            @NotNull UUID enrollmentUid,
            @NotNull UUID challengeUid,
            @NotBlank @Size(max = 16) String enrollmentKeyId,
            @NotBlank @Pattern(
                    regexp = "SELF_ENROLLMENT|LEGACY_ADOPTION")
            String enrollmentMode,
            @NotBlank @Size(min = 8, max = 64) String hardwareSn,
            @NotBlank @Size(max = 128) String identityPublicKey,
            @NotBlank @Size(max = 64) String responseWrapPublicKey,
            @NotBlank @Size(max = 128) String tunnelPublicKey,
            @NotBlank @Size(max = 128) String sshHostPublicKey,
            @NotBlank @Size(max = 64) String registrationMac,
            @NotBlank @Size(max = 128) String signature,
            @Size(max = 64) String legacyProof) {
    }

    public record EnrollmentView(
            int schemaVersion,
            UUID enrollmentUid,
            String status,
            Long retryAfterMs,
            JsonNode encryptedResponse,
            String failureCode) {
    }
}
