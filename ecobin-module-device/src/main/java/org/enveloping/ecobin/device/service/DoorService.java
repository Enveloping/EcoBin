package org.enveloping.ecobin.device.service;

import com.baomidou.mybatisplus.extension.service.IService;
import org.enveloping.ecobin.device.entity.Door;

import java.util.List;

/**
 * 投口服务接口
 */
public interface DoorService extends IService<Door> {

    /** 按设备ID查询投口列表 */
    List<Door> listByDeviceId(Long deviceId);
}
