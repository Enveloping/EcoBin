package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.security.JwtTokenProvider;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.application.web.TargetWebSessionService;
import org.enveloping.ecobin.identity.infrastructure.persistence.v1.TargetIdentitySessionRepository;
import org.junit.jupiter.api.Test;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class TargetWebSessionServiceTest {

    @Test
    void unknownAccountCannotMatchTheLegacyDefaultDummyPassword() {
        TargetIdentitySessionRepository repository =
                mock(TargetIdentitySessionRepository.class);
        when(repository.findStaffLogin("missing-account"))
                .thenReturn(Optional.empty());
        when(repository.findPlatformLogin("missing-account"))
                .thenReturn(Optional.empty());
        TargetWebSessionService service = new TargetWebSessionService(
                repository,
                mock(JwtTokenProvider.class),
                new BCryptPasswordEncoder());

        TargetApiException staff = assertThrows(
                TargetApiException.class,
                () -> service.loginStaff(
                        "missing-account",
                        "admin123",
                        null,
                        null,
                        null));
        TargetApiException platform = assertThrows(
                TargetApiException.class,
                () -> service.loginPlatform(
                        "missing-account",
                        "admin123",
                        null,
                        null,
                        null));

        assertEquals(401, staff.status());
        assertEquals("AUTH.INVALID_CREDENTIALS", staff.code());
        assertEquals(401, platform.status());
        assertEquals("AUTH.INVALID_CREDENTIALS", platform.code());
    }
}
