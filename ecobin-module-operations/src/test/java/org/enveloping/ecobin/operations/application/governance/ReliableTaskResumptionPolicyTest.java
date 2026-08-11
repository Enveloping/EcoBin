package org.enveloping.ecobin.operations.application.governance;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ReliableTaskResumptionPolicyTest {

    @Test
    void fundsInboxUsesTheActualProcessInboxTaskType() {
        assertTrue(ReliableTaskResumptionPolicy.supported(
                "FUNDS", "PROCESS_INBOX"));
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "FUNDS", "PROCESS_FUNDS_INBOX"));
    }

    @Test
    void unknownTasksAreNotAdvertisedAsResumable() {
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "FUNDS", "UNKNOWN_TASK"));
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "UNKNOWN_LANE", "PROCESS_INBOX"));
    }

    @Test
    void physicalStartsAndConfigurationUseSpecializedRecovery() {
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "DEVICE", "ENSURE_DEVICE_CONFIGURATION"));
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "DEVICE", "START_DELIVERY_SESSION"));
        assertFalse(ReliableTaskResumptionPolicy.supported(
                "DEVICE", "START_CLEAN_OPERATION"));
        assertTrue(ReliableTaskResumptionPolicy.supported(
                "DEVICE", "SAMPLE_FULLNESS"));
    }
}
