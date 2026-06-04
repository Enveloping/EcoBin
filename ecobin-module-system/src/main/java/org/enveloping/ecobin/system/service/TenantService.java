package org.enveloping.ecobin.system.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.system.entity.Tenant;

/**
 * 租户服务接口
 */
public interface TenantService extends IService<Tenant> {

    /** 按登录用户名查询 */
    Tenant getByUsername(String username);

    /** 按小程序 AppID 查询 */
    Tenant getByMiniappAppid(String appid);

    /** 解密某租户的小程序 Secret（明文）；无则返回 null */
    String decryptMiniappSecret(Tenant tenant);
}
