package org.enveloping.ecobin.funds.api.id;

import java.util.Objects;
import java.util.UUID;

public record WalletUid(UUID value) {

    public WalletUid {
        Objects.requireNonNull(value, "value");
    }
}
