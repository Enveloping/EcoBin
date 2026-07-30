package org.enveloping.ecobin.funds.api.query;

import java.time.Instant;

public record WalletEntryFilter(
        String entryType,
        Instant occurredFrom,
        Instant occurredTo,
        String sourceNo) {
}
