package org.enveloping.ecobin.funds.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

public record WalletEntryItem(
        UUID entryUid,
        OrganizationUserUid organizationUserUid,
        long entrySequenceNo,
        String entryType,
        long availableDeltaCent,
        long processingDeltaCent,
        long availableBalanceAfterCent,
        long withdrawalProcessingAfterCent,
        String sourceType,
        String sourceNo,
        Instant occurredAt) {

    public WalletEntryItem {
        Objects.requireNonNull(entryUid, "entryUid");
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        entryType = nonBlank(entryType, "entryType");
        sourceType = nonBlank(sourceType, "sourceType");
        sourceNo = nonBlank(sourceNo, "sourceNo");
        Objects.requireNonNull(occurredAt, "occurredAt");
        if (entrySequenceNo < 1) {
            throw new IllegalArgumentException(
                    "entrySequenceNo must be positive");
        }
        if (withdrawalProcessingAfterCent < 0) {
            throw new IllegalArgumentException(
                    "withdrawalProcessingAfterCent must not be negative");
        }
    }

    private static String nonBlank(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(name + " must not be blank");
        }
        return value;
    }
}
