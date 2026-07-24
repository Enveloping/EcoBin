package org.enveloping.ecobin.identity.api.legacy;

/**
 * F-02 迁移期微信用户首次注册公开端口。
 *
 * <p>后续身份模型落库后由正式注册命令替换。</p>
 */
public interface LegacyMiniappRegistrationPort {

    LegacyMiniappRegistrationResult registerOrFind(long tenantId, String openid, String unionid);
}
