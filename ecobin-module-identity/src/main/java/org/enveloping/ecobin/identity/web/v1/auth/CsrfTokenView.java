package org.enveloping.ecobin.identity.web.v1.auth;

public record CsrfTokenView(String headerName, String token) {
}
