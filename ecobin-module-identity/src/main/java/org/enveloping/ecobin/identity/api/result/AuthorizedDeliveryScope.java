package org.enveloping.ecobin.identity.api.result;

import org.enveloping.ecobin.identity.api.persistence.DeliveryScopePersistenceRef;

import java.util.Objects;
import java.util.UUID;

/**
 * Safe recycling-business authorization result.
 *
 * <p>Public identities and capability decisions are available to the caller.
 * Internal relationship keys remain inside the transaction-bound,
 * single-consumption persistence reference.</p>
 */
public record AuthorizedDeliveryScope(
        boolean platformActor,
        UUID principalUid,
        UUID sessionUid,
        String actorDisplayName,
        String tenantCode,
        String organizationCode,
        boolean deliveryRead,
        boolean reviewExecute,
        boolean deliveryCorrect,
        boolean deliveryConfigurationManage,
        boolean cleanRead,
        boolean cleanEdit,
        DeliveryScopePersistenceRef persistenceRef) {

    public AuthorizedDeliveryScope {
        Objects.requireNonNull(principalUid, "principalUid");
        Objects.requireNonNull(sessionUid, "sessionUid");
        Objects.requireNonNull(actorDisplayName, "actorDisplayName");
        Objects.requireNonNull(tenantCode, "tenantCode");
        Objects.requireNonNull(organizationCode, "organizationCode");
        Objects.requireNonNull(persistenceRef, "persistenceRef");
        if (!deliveryRead
                && !reviewExecute
                && !deliveryCorrect
                && !deliveryConfigurationManage
                && !cleanRead && !cleanEdit) {
            throw new IllegalArgumentException(
                    "authorized business scope requires a capability");
        }
    }
}
