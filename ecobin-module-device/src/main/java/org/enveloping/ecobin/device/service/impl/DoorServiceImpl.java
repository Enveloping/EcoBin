package org.enveloping.ecobin.device.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import org.enveloping.ecobin.device.entity.Door;
import org.enveloping.ecobin.device.mapper.DoorMapper;
import org.enveloping.ecobin.device.service.DoorService;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class DoorServiceImpl extends ServiceImpl<DoorMapper, Door> implements DoorService {

    @Override
    public List<Door> listByDeviceId(Long deviceId) {
        return baseMapper.selectByDeviceId(deviceId);
    }
}
