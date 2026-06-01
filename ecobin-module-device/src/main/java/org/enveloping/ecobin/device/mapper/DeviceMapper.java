package org.enveloping.ecobin.device.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.enveloping.ecobin.device.entity.Device;

@Mapper
public interface DeviceMapper extends BaseMapper<Device> {
}
