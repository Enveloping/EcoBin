package org.enveloping.ecobin.device.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.mapper.DeviceMapper;
import org.enveloping.ecobin.device.service.DeviceService;
import org.springframework.stereotype.Service;

@Service
public class DeviceServiceImpl extends ServiceImpl<DeviceMapper, Device> implements DeviceService {

    @Override
    public PageResult<Device> pageDevices(int page, int pageSize) {
        Page<Device> p = new Page<>(page, pageSize);
        Page<Device> result = page(p, new LambdaQueryWrapper<Device>().orderByDesc(Device::getId));
        return PageResult.of(result.getRecords(), result.getTotal(), page, pageSize);
    }
}
