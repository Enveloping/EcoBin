package org.enveloping.ecobin.identity.web.v1.miniapp;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class FactoryMiniappModels {

    private FactoryMiniappModels() {
    }

    public record FactoryMiniappLoginRequest(
            @NotBlank
            @Pattern(regexp = "^wx[0-9a-zA-Z]{16}$")
            String appId,
            @NotBlank @Size(max = 256) String wxLoginCode,
            @Size(max = 128) String bindingToken) {
    }

    public record FactoryMiniappSessionCreated(
            String accessToken,
            String tokenType,
            String audience,
            String entryMode,
            Instant expiresAt,
            UUID factoryOperatorUid,
            String operatorCode,
            String displayName,
            List<String> capabilities,
            boolean newlyBound) {

        public FactoryMiniappSessionCreated {
            capabilities = List.copyOf(capabilities);
        }
    }

    public record FactoryMiniappSessionView(
            String audience,
            String entryMode,
            Instant expiresAt,
            UUID factoryOperatorUid,
            String operatorCode,
            String displayName,
            List<String> capabilities) {

        public FactoryMiniappSessionView {
            capabilities = List.copyOf(capabilities);
        }
    }
}
