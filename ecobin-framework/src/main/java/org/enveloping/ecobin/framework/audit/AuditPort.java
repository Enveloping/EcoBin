package org.enveloping.ecobin.framework.audit;

import java.util.Optional;
import java.util.UUID;

/**
 * Stable technical boundary implemented by operations. Callers participate in
 * their existing REQUIRED transaction so successful audit and business state
 * commit atomically.
 */
public interface AuditPort {

    Optional<SuccessfulAudit> findSuccessful(UUID operationUid);

    void append(AuditEntry entry);
}
