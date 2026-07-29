package org.enveloping.ecobin.funds.api.result;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;

import java.util.List;
import java.util.Objects;

/**
 * 当前钱包的只读投递资格快照。
 */
public record DeliveryWalletQualification(
        OrganizationUserUid organizationUserUid,
        long availableBalanceCent,
        long openBalanceFloorCent,
        boolean eligible,
        List<DeliveryWalletQualificationBlocker> blockers) {

    public DeliveryWalletQualification {
        Objects.requireNonNull(
                organizationUserUid,
                "organizationUserUid");
        blockers = List.copyOf(blockers);
        if (openBalanceFloorCent >= 0) {
            throw new IllegalArgumentException(
                    "openBalanceFloorCent must be negative");
        }
        if (eligible != blockers.isEmpty()) {
            throw new IllegalArgumentException(
                    "eligible must match an empty blocker list");
        }
    }
}
