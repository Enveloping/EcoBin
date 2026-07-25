package org.enveloping.ecobin.operations.application.legacy;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.common.constant.Constants;
import org.enveloping.ecobin.device.api.legacy.LegacyDeviceStatisticsPort;
import org.enveloping.ecobin.funds.api.legacy.LegacyFundsStatistics;
import org.enveloping.ecobin.funds.api.legacy.LegacyFundsStatisticsPort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserFinancePort;
import org.enveloping.ecobin.identity.api.legacy.LegacyOrganizationUserStatistics;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningStatistics;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningStatisticsPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryStatistics;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryStatisticsPort;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class StatisticsServiceImpl implements StatisticsService {

    private final LegacyDeliveryStatisticsPort deliveryStatisticsPort;
    private final LegacyCleaningStatisticsPort cleaningStatisticsPort;
    private final LegacyOrganizationUserFinancePort organizationUserFinancePort;
    private final LegacyFundsStatisticsPort fundsStatisticsPort;
    private final LegacyDeviceStatisticsPort deviceStatisticsPort;

    @Override
    public Map<String, Object> dashboard() {
        LegacyDeliveryStatistics statistics = deliveryStatisticsPort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("deliveryCount", statistics.todayCount());
        result.put("totalWeight", statistics.todayWeight());
        result.put("todayMemberCount", statistics.todayMemberCount());
        return result;
    }

    @Override
    public Map<String, Object> deviceStats() {
        Map<String, Object> result = new HashMap<>();
        result.put("totalCount", deviceStatisticsPort.countDevices());
        result.put("onlinkCount", 0);
        result.put("spillCount", 0);
        result.put("smokeCount", 0);
        return result;
    }

    @Override
    public Map<String, Object> memberStats() {
        LegacyOrganizationUserStatistics statistics = organizationUserFinancePort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("memberCount", statistics.memberCount());
        result.put("todayMemberCount", statistics.todayMemberCount());
        result.put("memberDisableCount", statistics.disabledMemberCount());
        return result;
    }

    @Override
    public Map<String, Object> deliveryStats() {
        LegacyDeliveryStatistics statistics = deliveryStatisticsPort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("deliveryCount", statistics.monthCount());
        result.put("deliveryWeight", statistics.monthWeight());
        result.put("deliveryMoney", statistics.monthMoney());
        result.put("minusyMoney", 0);
        return result;
    }

    @Override
    public Map<String, Object> cleanStats() {
        LegacyCleaningStatistics statistics = cleaningStatisticsPort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("cleanCount", statistics.monthCount());
        result.put("totalWeights", statistics.monthWeight());
        result.put("storageWeights", 0);
        result.put("minusyWeights", 0);
        return result;
    }

    @Override
    public Map<String, Object> payoutStats() {
        LegacyFundsStatistics statistics = fundsStatisticsPort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("payOutCount", statistics.withdrawCount());
        result.put("payOutMoney", toDouble(statistics.requestedAmount()));
        result.put("pushSuccessMoney", toDouble(statistics.approvedAmount()));
        result.put("refundUserMoney", 0);
        return result;
    }

    @Override
    public Map<String, Object> memberMoneyStats() {
        LegacyOrganizationUserStatistics statistics = organizationUserFinancePort.statistics();
        Map<String, Object> result = new HashMap<>();
        result.put("memberCount", statistics.memberCount());
        result.put("memberMoney", toDouble(statistics.balanceTotal()));
        result.put("memberPlanMoney", toDouble(statistics.pendingBalanceTotal()));
        result.put("memberScore", 0);
        return result;
    }

    @Override
    public List<Map<String, Object>> devicesMap() {
        return deviceStatisticsPort.locatedDevices().stream().map(device -> {
            Map<String, Object> row = new HashMap<>();
            row.put("id", device.id().value());
            row.put("sn", device.sn());
            row.put("name", device.name());
            row.put("lat", device.lat());
            row.put("lng", device.lng());
            row.put("onLink", 0);
            row.put("spillNow", 0);
            row.put("smoke", 0);
            return row;
        }).toList();
    }

    @Override
    public List<Map<String, Object>> deviceRanking(int pageSize) {
        int boundedPageSize = pageSize <= 0 ? 5 : Math.min(pageSize, Constants.MAX_PAGE_SIZE);
        return deliveryStatisticsPort.deviceRanking(boundedPageSize).stream()
                .map(ranking -> {
                    Map<String, Object> row = new HashMap<>();
                    row.put("device_id", ranking.deviceId().value());
                    row.put("device_name", ranking.deviceName());
                    row.put("total_weight", ranking.totalWeight());
                    return row;
                })
                .toList();
    }

    private static double toDouble(BigDecimal value) {
        return value == null ? 0.0 : value.doubleValue();
    }
}
