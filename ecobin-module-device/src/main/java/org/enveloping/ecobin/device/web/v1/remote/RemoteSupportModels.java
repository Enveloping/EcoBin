package org.enveloping.ecobin.device.web.v1.remote;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.UUID;

public final class RemoteSupportModels {

    private RemoteSupportModels() {
    }

    public record OpenRemoteSupportRequest(
            @NotNull UUID maintenanceSshKeyUid,
            @NotNull @Min(300) @Max(1800) Integer lifetimeSeconds,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record CloseRemoteSupportRequest(
            @NotBlank @Size(max = 500) String reason) {
    }

    public record RemoteSupportSessionView(
            UUID sessionUid,
            String hardwareSn,
            UUID maintenanceSshKeyUid,
            String state,
            int remotePort,
            Instant connectDeadlineAt,
            Instant expiresAt,
            Instant openedAt,
            Instant closedAt,
            String failureCode,
            String certificate,
            String bastionHost,
            int bastionSshPort,
            String bastionUser,
            String targetUser,
            String hostKeyAlias,
            String knownHostsLine,
            String sshCommand,
            long version) {
    }
}
