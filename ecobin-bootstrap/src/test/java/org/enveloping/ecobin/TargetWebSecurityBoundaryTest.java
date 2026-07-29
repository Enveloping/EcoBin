package org.enveloping.ecobin;

import jakarta.servlet.http.Cookie;
import org.enveloping.ecobin.framework.context.TrustedAudience;
import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.time.Instant;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@SpringBootTest
@AutoConfigureMockMvc
@ActiveProfiles("test")
class TargetWebSecurityBoundaryTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtTokenProvider tokenProvider;

    @Test
    void loginIsRejectedBeforeCredentialLookupWithoutCsrf() throws Exception {
        mockMvc.perform(post("/api/v1/web/platform/auth/sessions")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {
                                  "loginName": "does-not-matter",
                                  "password": "does-not-matter"
                                }
                                """))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("SECURITY.CSRF_INVALID"));
    }

    @Test
    void malformedOrWrongAudienceCredentialsCannotAuthorizeTargetRoutes()
            throws Exception {
        mockMvc.perform(get("/api/v1/web/auth/sessions/current")
                        .cookie(new Cookie(
                                "__Host-ecobin-web-session",
                                "malformed-token")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("AUTH.SESSION_INVALID"));

        Instant issuedAt = Instant.now();
        String miniappToken = tokenProvider.generateTargetMiniappToken(
                UUID.randomUUID(),
                UUID.randomUUID(),
                TrustedAudience.MINIAPP,
                issuedAt,
                issuedAt.plusSeconds(300));
        mockMvc.perform(get("/api/v1/web/platform/auth/sessions/current")
                        .cookie(new Cookie(
                                "__Host-ecobin-web-session",
                                miniappToken)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("AUTH.SESSION_INVALID"));

        mockMvc.perform(get("/api/v1/web/platform/auth/sessions/current")
                        .header(
                                "Authorization",
                                "Bearer " + miniappToken))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void csrfBootstrapCookieIsSecureAndReadableByTheSpa() throws Exception {
        MvcResult result = mockMvc.perform(
                        get("/api/v1/web/auth/csrf-token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.headerName")
                        .value("X-CSRF-TOKEN"))
                .andReturn();
        Cookie csrf = result.getResponse().getCookie("ecobin-csrf");
        assertTrue(csrf != null && csrf.getSecure());
        assertFalse(csrf != null && csrf.isHttpOnly());
        assertTrue(csrf != null && "/".equals(csrf.getPath()));
    }
}
