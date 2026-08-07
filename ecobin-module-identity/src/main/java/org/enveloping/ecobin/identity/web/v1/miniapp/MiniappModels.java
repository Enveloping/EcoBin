package org.enveloping.ecobin.identity.web.v1.miniapp;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class MiniappModels {

    private MiniappModels() {
    }

    public record RegistrationSource(
            @NotBlank
            @Size(max = 64)
            @Pattern(regexp = "^Dv_[A-Za-z0-9_-]{24,61}$")
            String deviceCode) {
    }

    public record MiniappLoginRequest(
            @NotBlank
            @Pattern(regexp = "^wx[0-9a-zA-Z]{16}$")
            String appId,
            @NotBlank
            @Size(max = 256)
            String wxLoginCode,
            @Valid
            RegistrationSource registrationSource) {
    }

    public record OrganizationSummary(
            String organizationCode,
            String displayName) {
    }

    public record MiniappSessionView(
            String audience,
            String entryMode,
            Instant expiresAt,
            OrganizationSummary organization,
            UUID subjectUid,
            String displayName,
            List<String> capabilities,
            boolean phoneBound) {

        public MiniappSessionView {
            capabilities = List.copyOf(capabilities);
        }
    }

    public record MiniappSessionCreated(
            String accessToken,
            String tokenType,
            String audience,
            String entryMode,
            Instant expiresAt,
            OrganizationSummary organization,
            UUID subjectUid,
            String displayName,
            List<String> capabilities,
            boolean phoneBound,
            boolean isNewRegistration) {

        public MiniappSessionCreated {
            capabilities = List.copyOf(capabilities);
        }
    }

    public record BindPhoneRequest(
            @NotBlank
            @Size(max = 256)
            String wechatPhoneCode) {
    }

    public record PhoneBindingView(
            boolean phoneBound,
            String maskedPhoneNumber,
            Instant phoneBoundAt) {
    }

    public record PhoneBindingResult(
            PhoneBindingView binding,
            boolean created) {
    }
}
