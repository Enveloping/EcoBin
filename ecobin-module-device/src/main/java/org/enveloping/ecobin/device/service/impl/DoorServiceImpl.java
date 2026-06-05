package org.enveloping.ecobin.device.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.exception.BusinessException;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.mapper.DeviceMapper;
import org.enveloping.ecobin.device.mapper.DoorMapper;
import org.enveloping.ecobin.device.service.DoorService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class DoorServiceImpl extends ServiceImpl<DoorMapper, Door> implements DoorService {

    private final DeviceMapper deviceMapper;

    public DoorServiceImpl(DeviceMapper deviceMapper) {
        this.deviceMapper = deviceMapper;
    }

    @Override
    public List<Door> listByDeviceId(Long deviceId) {
        return baseMapper.selectByDeviceId(deviceId);
    }

    @Override
    public boolean save(Door door) {
        // 平台域（管理员）创建：上下文放行租户过滤、不自动回填，默认归入平台池（待分配）。
        // 租户域创建：保持 tenantId 为空，交由租户拦截器按上下文自动回填，勿强制覆盖。
        if (door.getTenantId() == null && TenantContextHolder.isIgnore()) {
            door.setTenantId(Constants.PLATFORM_POOL_TENANT_ID);
        }

        // 校验所属设备存在
        if (door.getDeviceId() == null) {
            throw new BusinessException(400, "所属设备ID不能为空");
        }
        if (deviceMapper.selectById(door.getDeviceId()) == null) {
            throw new BusinessException(400, "所属设备不存在");
        }

        // 检查同一设备下 door_index 是否重复
        if (door.getDoorIndex() != null) {
            LambdaQueryWrapper<Door> wrapper = new LambdaQueryWrapper<Door>()
                    .eq(Door::getDeviceId, door.getDeviceId())
                    .eq(Door::getDoorIndex, door.getDoorIndex());
            if (baseMapper.selectCount(wrapper) > 0) {
                throw new BusinessException(400, "该设备下已存在相同投口号");
            }
        }

        return super.save(door);
    }

    @Override
    public boolean updateById(Door door) {
        Door existing = baseMapper.selectById(door.getId());
        if (existing == null) {
            throw new BusinessException(404, "投口不存在");
        }

        // 如果修改了 deviceId，校验新设备存在
        if (door.getDeviceId() != null && !door.getDeviceId().equals(existing.getDeviceId())) {
            if (deviceMapper.selectById(door.getDeviceId()) == null) {
                throw new BusinessException(400, "所属设备不存在");
            }
            // 合并修改 door_index 的场景，用新 deviceId 检查唯一性
        }

        // 检查同一设备下 door_index 是否重复（排除自身）
        if (door.getDoorIndex() != null) {
            Long effectiveDeviceId = door.getDeviceId() != null ? door.getDeviceId() : existing.getDeviceId();
            LambdaQueryWrapper<Door> wrapper = new LambdaQueryWrapper<Door>()
                    .eq(Door::getDeviceId, effectiveDeviceId)
                    .eq(Door::getDoorIndex, door.getDoorIndex())
                    .ne(Door::getId, door.getId());
            if (baseMapper.selectCount(wrapper) > 0) {
                throw new BusinessException(400, "该设备下已存在相同投口号");
            }
        }

        return super.updateById(door);
    }
}
