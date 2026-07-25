package org.enveloping.ecobin.recycling.api.legacy;

import java.util.List;

/**
 * operations 读取旧投递事实的只读端口。
 */
public interface LegacyDeliveryStatisticsPort {

    LegacyDeliveryStatistics statistics();

    List<LegacyDeviceDeliveryRanking> deviceRanking(int pageSize);
}
