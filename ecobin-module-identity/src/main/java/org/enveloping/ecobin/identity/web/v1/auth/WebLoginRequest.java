package org.enveloping.ecobin.identity.web.v1.auth;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record WebLoginRequest(
        @NotBlank @Size(max = 128) String loginName,
        @NotBlank @Size(max = 256) String password) {
}
