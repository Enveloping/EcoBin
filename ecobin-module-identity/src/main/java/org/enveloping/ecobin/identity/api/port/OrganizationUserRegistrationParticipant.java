package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.command.OrganizationUserRegistrationCommand;

/**
 * 首次机构用户创建唯一同步参与者。实现必须以 REQUIRED 加入 identity 事务。
 */
public interface OrganizationUserRegistrationParticipant {

    void initializeWallet(OrganizationUserRegistrationCommand command);
}
