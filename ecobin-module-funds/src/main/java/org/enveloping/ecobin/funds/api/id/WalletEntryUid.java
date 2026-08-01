package org.enveloping.ecobin.funds.api.id;

import java.util.Objects;
import java.util.UUID;

/**
 * 对外稳定的钱包明细标识。
 */
public record WalletEntryUid(UUID value) {

    public WalletEntryUid {
        Objects.requireNonNull(value, "value");
    }
}
