package org.enveloping.ecobin.device.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.device.entity.DeviceSession;
import org.enveloping.ecobin.device.mapper.DeviceSessionMapper;
import org.enveloping.ecobin.device.service.DeviceSessionService;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;

@Service
public class DeviceSessionServiceImpl extends ServiceImpl<DeviceSessionMapper, DeviceSession>
        implements DeviceSessionService {

    /** 会话有效期（分钟）：开启设备后这段时间内的设备上报都归该用户，超时即无主。 */
    private static final long TTL_MINUTES = 15;

    @Override
    public void activate(Long deviceId, Long tenantId, Long userId, Integer loginType) {
        LocalDateTime expire = LocalDateTime.now().plusMinutes(TTL_MINUTES);
        DeviceSession existing = lambdaQuery().eq(DeviceSession::getDeviceId, deviceId).one();
        if (existing == null) {
            DeviceSession session = new DeviceSession();
            session.setTenantId(tenantId);
            session.setDeviceId(deviceId);
            session.setUserId(userId);
            session.setLoginType(loginType);
            session.setExpireTime(expire);
            save(session);
        } else {
            // 覆盖为「最近用户」
            existing.setTenantId(tenantId);
            existing.setUserId(userId);
            existing.setLoginType(loginType);
            existing.setExpireTime(expire);
            updateById(existing);
        }
    }

    @Override
    public DeviceSession findActive(Long deviceId) {
        DeviceSession session = lambdaQuery().eq(DeviceSession::getDeviceId, deviceId).one();
        if (session == null || session.getExpireTime() == null
                || session.getExpireTime().isBefore(LocalDateTime.now())) {
            return null;
        }
        return session;
    }

    @Override
    public void refresh(Long deviceId) {
        DeviceSession session = lambdaQuery().eq(DeviceSession::getDeviceId, deviceId).one();
        if (session != null) {
            session.setExpireTime(LocalDateTime.now().plusMinutes(TTL_MINUTES));
            updateById(session);
        }
    }
}
