package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.CleanAuditActorRef;
import org.enveloping.ecobin.identity.api.persistence.CleanOrganizationScopeRef;
import org.enveloping.ecobin.identity.api.persistence.CleanOrganizationUserRef;

/** identity 内部用于发行清运复合事务关系引用的桥。 */
public interface StartCleanPersistenceRefFactory {

    CleanOrganizationScopeRef issueOrganizationScope(
            long tenantKey,
            long organizationKey);

    CleanOrganizationUserRef issueOrganizationUser(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);

    CleanAuditActorRef issueAuditActor(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);
}
