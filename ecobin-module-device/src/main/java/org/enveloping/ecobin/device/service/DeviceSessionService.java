package org.enveloping.ecobin.device.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.device.entity.DeviceSession;

/**
 * 设备活跃会话服务：维护一台设备「当前活跃用户」，支撑投递「上传后建单」时的用户归属。
 */
public interface DeviceSessionService extends IService<DeviceSession> {

    /**
     * 用户「开启设备」：按 {@code deviceId} upsert 活跃会话，过期时间顺延 TTL。
     * 同一设备被后来用户开启会覆盖上一行（接受「最近用户」语义）。
     */
    void activate(Long deviceId, Long tenantId, Long userId, Integer loginType);

    /**
     * 取设备当前「未过期」的活跃会话；过期或从未开启返回 {@code null}。
     * 设备上报无租户上下文，按 {@code deviceId} 全局查询（租户拦截器放行）。
     */
    DeviceSession findActive(Long deviceId);

    /**
     * 续期：投递成功后顺延会话过期时间，保持连续投递窗口（无会话则不处理）。
     */
    void refresh(Long deviceId);
}
