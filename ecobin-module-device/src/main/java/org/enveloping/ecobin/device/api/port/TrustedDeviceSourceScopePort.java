package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;

/** 根据 OneNet 已认证设备名解析资产级或机构业务级可信范围。 */
public interface TrustedDeviceSourceScopePort {

    /** 设备在线状态和机器验收永远属于平台资产，不依赖租户或机构归属。 */
    TrustedInboxScopeResolver resolverForPlatformAsset(String hardwareSn);

    /** 依据已冻结的确认任务决定回执属于平台资产还是机构业务。 */
    TrustedInboxScopeResolver resolverForBusinessConfirmation(
            String hardwareSn,
            String confirmationUid);

    /** 只有已经永久分配到机构的设备才可提交日常业务事件。 */
    TrustedInboxScopeResolver resolverForOrganizationAsset(String hardwareSn);
}
