package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.command.OrganizationBootstrapCommand;

/**
 * 新机构同步初始化参与者。
 *
 * <p>实现必须以 REQUIRED 加入 identity 创建机构的事务。任何参与者失败时，
 * 机构主记录和所有已经写入的业务初始化记录必须一起回滚。</p>
 */
public interface OrganizationBootstrapParticipant {

    void initializeOrganization(OrganizationBootstrapCommand command);
}
