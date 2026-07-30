package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.OrganizationBootstrapPersistenceRef;

/**
 * identity 内部创建机构用例使用的 FK 构造引用发行桥。
 */
public interface OrganizationBootstrapPersistenceRefFactory {

    OrganizationBootstrapPersistenceRef issue(
            long tenantKey,
            long organizationKey);
}
