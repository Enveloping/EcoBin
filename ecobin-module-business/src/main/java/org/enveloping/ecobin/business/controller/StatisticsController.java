package org.enveloping.ecobin.business.controller;

import lombok.RequiredArgsConstructor;
import org.enveloping.ecobin.business.service.StatisticsService;
import org.enveloping.ecobin.common.result.Result;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 统计接口（超管 + 租户）。
 * 租户隔离由 TenantLineInnerInterceptor 自动注入 tenant_id 过滤。
 */
@RestController
@RequestMapping("/api/statistics")
@RequiredArgsConstructor
public class StatisticsController {

    private final StatisticsService statisticsService;

    /** 今日概览：投递次数 + 总重量 + 投递人数 */
    @GetMapping("/dashboard")
    public Result<Map<String, Object>> dashboard() {
        return Result.ok(statisticsService.dashboard());
    }

    /** 设备信息统计 */
    @GetMapping("/devices")
    public Result<Map<String, Object>> devices() {
        return Result.ok(statisticsService.deviceStats());
    }

    /** 会员统计 */
    @GetMapping("/members")
    public Result<Map<String, Object>> members() {
        return Result.ok(statisticsService.memberStats());
    }

    /** 本月投递统计 */
    @GetMapping("/delivery")
    public Result<Map<String, Object>> delivery() {
        return Result.ok(statisticsService.deliveryStats());
    }

    /** 本月清运统计 */
    @GetMapping("/clean")
    public Result<Map<String, Object>> clean() {
        return Result.ok(statisticsService.cleanStats());
    }

    /** 提现支出统计 */
    @GetMapping("/payout")
    public Result<Map<String, Object>> payout() {
        return Result.ok(statisticsService.payoutStats());
    }

    /** 会员资金统计 */
    @GetMapping("/member-money")
    public Result<Map<String, Object>> memberMoney() {
        return Result.ok(statisticsService.memberMoneyStats());
    }

    /** 设备地图坐标 */
    @GetMapping("/devices-map")
    public Result<List<Map<String, Object>>> devicesMap() {
        return Result.ok(statisticsService.devicesMap());
    }

    /** 本月设备投递排行（按重量降序，默认取前 5） */
    @GetMapping("/device-ranking")
    public Result<List<Map<String, Object>>> deviceRanking(@RequestParam(defaultValue = "5") int pageSize) {
        return Result.ok(statisticsService.deviceRanking(pageSize));
    }
}
