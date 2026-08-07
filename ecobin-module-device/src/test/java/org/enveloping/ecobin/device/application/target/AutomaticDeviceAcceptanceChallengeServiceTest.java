package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class AutomaticDeviceAcceptanceChallengeServiceTest {

    @Test
    void challengeJoinsTheOnlineInboxTransaction() throws Exception {
        Transactional transaction =
                AutomaticDeviceAcceptanceChallengeService.class
                        .getMethod("requestIfNeeded", long.class)
                        .getAnnotation(Transactional.class);

        assertNotNull(transaction);
        assertEquals(Propagation.REQUIRED, transaction.propagation());
        assertEquals(Isolation.READ_COMMITTED, transaction.isolation());
    }
}
