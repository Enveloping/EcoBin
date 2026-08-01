package org.enveloping.ecobin.funds.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.util.Objects;

public record WalletBalanceSnapshot(
        OrganizationUserUid organizationUserUid,
        long walletVersion,
        long availableBalanceCent,
        long withdrawalProcessingCent) {

    public WalletBalanceSnapshot {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        if (walletVersion < 0) {
            throw new IllegalArgumentException(
                    "walletVersion must not be negative");
        }
        if (withdrawalProcessingCent < 0) {
            throw new IllegalArgumentException(
                    "withdrawalProcessingCent must not be negative");
        }
    }
}
