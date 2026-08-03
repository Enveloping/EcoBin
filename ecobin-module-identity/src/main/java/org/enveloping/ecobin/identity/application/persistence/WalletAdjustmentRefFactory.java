package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentScopeRef;
import org.enveloping.ecobin.identity.api.persistence.WalletAdjustmentTargetRef;

import java.util.UUID;

public interface WalletAdjustmentRefFactory {

    WalletAdjustmentScopeRef issueScope(long tenantId, long organizationId);

    WalletAdjustmentTargetRef issueTarget(
            long tenantId,
            long organizationId,
            long organizationUserId,
            UUID organizationUserUid,
            Long platformAdminId,
            Long staffAccountId);
}
