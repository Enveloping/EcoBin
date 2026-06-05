package org.enveloping.ecobin.device.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.mapper.DeviceMapper;
import org.enveloping.ecobin.device.mapper.DoorMapper;
import org.enveloping.ecobin.device.service.DeviceService;
import org.enveloping.ecobin.framework.tenant.TenantContextHolder;
import org.springframework.stereotype.Service;

@Service
public class DeviceServiceImpl extends ServiceImpl<DeviceMapper, Device> implements DeviceService {

    private final DoorMapper doorMapper;

    public DeviceServiceImpl(DoorMapper doorMapper) {
        this.doorMapper = doorMapper;
    }

    @Override
    public boolean save(Device device) {
        // 平台域（管理员）创建：上下文放行租户过滤、不自动回填，默认归入平台池（待分配）。
        // 租户域创建：保持 tenantId 为空，交由租户拦截器按上下文自动回填，勿强制覆盖。
        if (device.getTenantId() == null && TenantContextHolder.isIgnore()) {
            device.setTenantId(Constants.PLATFORM_POOL_TENANT_ID);
        }
        boolean saved = super.save(device);

        // 创建设备时自动创建默认投口
        Door defaultDoor = new Door();
        defaultDoor.setDeviceId(device.getId());
        defaultDoor.setDoorIndex(1);
        defaultDoor.setName("默认投口");
        defaultDoor.setWasteType1(4);  // 其他
        defaultDoor.setWasteType2(0);  // 不区分
        defaultDoor.setEnabled(1);
        defaultDoor.setSortOrder(0);
        defaultDoor.setTenantId(device.getTenantId());
        doorMapper.insert(defaultDoor);

        return saved;
    }

    @Override
    public PageResult<Device> pageDevices(int page, int pageSize) {
        Page<Device> p = new Page<>(page, pageSize);
        Page<Device> result = page(p, new LambdaQueryWrapper<Device>().orderByDesc(Device::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }

    @Override
    public boolean removeById(java.io.Serializable id) {
        // 级联删除该设备下的所有投口
        doorMapper.delete(new LambdaQueryWrapper<Door>().eq(Door::getDeviceId, id));
        return super.removeById(id);
    }
}
