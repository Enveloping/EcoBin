package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;

/** 开始清运复合事务中的 identity 同步参与端口。 */
public interface StartCleanIdentityParticipationPort {

    LockedMiniappCleanScope lockCurrentMiniappScope();

    LockedCleanOrganizationUser lockCurrentCleaner(
            LockedMiniappCleanScope lockedScope);
}
