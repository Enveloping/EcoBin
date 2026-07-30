package org.enveloping.ecobin.funds.api.result;

import java.time.Instant;
import java.util.List;
import java.util.Objects;

public record WalletEntryPage(
        List<WalletEntryItem> items,
        Instant asOf,
        String nextCursor) {

    public WalletEntryPage {
        items = List.copyOf(items);
        Objects.requireNonNull(asOf, "asOf");
    }
}
