package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class InterruptedCleanBagRecoveryDecisionTest {

    @Test
    void recognizesOnlyTheFrozenOldBagOrTheOperationsReservedNewBag()
            throws Exception {
        InterruptedCleanBagRecoveryService.Target target = target(
                "EB1_K1_OLD_BAG_CODE",
                "EB1_K1_RESERVED_NEW_BAG_CODE");

        assertEquals(
                "RETAIN_OLD_BAG",
                decide(target, "EB1_K1_OLD_BAG_CODE").toString());
        assertEquals(
                "USE_RESERVED_NEW_BAG",
                decide(target,
                        "EB1_K1_RESERVED_NEW_BAG_CODE").toString());
        assertNull(decide(target, "EB1_K1_UNRELATED_THIRD_BAG"));
    }

    private static Object decide(
            InterruptedCleanBagRecoveryService.Target target,
            String bagCode) throws Exception {
        Method method = InterruptedCleanBagRecoveryService.class
                .getDeclaredMethod(
                        "decide",
                        InterruptedCleanBagRecoveryService.Target.class,
                        String.class);
        method.setAccessible(true);
        return method.invoke(null, target, bagCode);
    }

    private static InterruptedCleanBagRecoveryService.Target target(
            String oldBagCode,
            String newBagCode) {
        return new InterruptedCleanBagRecoveryService.Target(
                1L,
                UUID.fromString(
                        "81000000-0000-4000-8000-000000000001"),
                2L,
                3L,
                13L,
                1,
                "V81-DECISION-TEST",
                "ABORTED",
                0L,
                "MCU_COMMUNICATION_UNAVAILABLE",
                4L,
                UUID.fromString(
                        "81000000-0000-4000-8000-000000000004"),
                oldBagCode,
                5L,
                1200L,
                6L,
                UUID.fromString(
                        "81000000-0000-4000-8000-000000000006"),
                newBagCode,
                null,
                7L,
                8L,
                "aa".repeat(32),
                "bb".repeat(32),
                9L,
                10L,
                "WEIGHT_ONLY",
                50000L,
                0L,
                1000L,
                5000L,
                0L,
                4L,
                "VALID",
                5L,
                1200L,
                4L,
                1200L,
                8L,
                "aa".repeat(32),
                "bb".repeat(32),
                11L,
                12L);
    }
}
