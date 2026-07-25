package org.enveloping.ecobin.device.api.legacy;

import java.util.List;

/**
 * operations 旧统计页读取 device 事实的只读端口。
 */
public interface LegacyDeviceStatisticsPort {

    long countDevices();

    List<LegacyDeviceLocationSnapshot> locatedDevices();
}
