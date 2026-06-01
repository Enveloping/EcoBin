package org.enveloping.ecobin.device.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.common.result.PageResult;
import org.enveloping.ecobin.device.entity.Device;

/**
 * 设备服务接口
 */
public interface DeviceService extends IService<Device> {

    /** 分页查询 */
    PageResult<Device> pageDevices(int page, int pageSize);
}
