package org.enveloping.ecobin.identity.application.persistence;

import org.enveloping.ecobin.identity.api.persistence.OrganizationUserWalletOwnerRef;

/**
 * identity 内部首次注册用例使用的 FK 构造引用发行桥。
 *
 * <p>该类型不属于模块公开 API；其他模块不得导入或取得它。</p>
 */
public interface OrganizationUserWalletOwnerRefFactory {

    OrganizationUserWalletOwnerRef issue(
            long tenantKey,
            long organizationKey,
            long organizationUserKey);
}
