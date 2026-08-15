package org.enveloping.ecobin.framework.idempotency;

import java.util.UUID;

/**
 * Global operation-UID ledger shared by transactional application services.
 *
 * <p>Both methods must be called inside the same outer business transaction.
 * A failed outer transaction therefore removes a newly acquired claim together
 * with the failed business write.</p>
 */
public interface GlobalOperationIdempotencyPort {

    GlobalOperationClaim claim(GlobalOperationBinding binding);

    void succeed(UUID operationUid, GlobalOperationResult result);
}
