package org.enveloping.ecobin.device.application.legacy;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceId;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceLocationSnapshot;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceStatisticsPort;
import org.enveloping.ecobin.device.entity.Device;
import org.enveloping.ecobin.device.mapper.DeviceMapper;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * operations 旧概览的 device 私有查询适配器。
 */
@Component
@RequiredArgsConstructor
public class LegacyDeviceStatisticsAdapter implements LegacyDeviceStatisticsPort {

    private final DeviceMapper deviceMapper;

    @Override
    public long countDevices() {
        return deviceMapper.selectCount(new LambdaQueryWrapper<>());
    }

    @Override
    public List<LegacyDeviceLocationSnapshot> locatedDevices() {
        return deviceMapper.selectList(new LambdaQueryWrapper<Device>()
                        .isNotNull(Device::getLat)
                        .isNotNull(Device::getLng))
                .stream()
                .map(device -> new LegacyDeviceLocationSnapshot(
                        new LegacyDeviceId(device.getId()),
                        device.getSn(),
                        device.getName(),
                        device.getLat(),
                        device.getLng()))
                .toList();
    }
}
