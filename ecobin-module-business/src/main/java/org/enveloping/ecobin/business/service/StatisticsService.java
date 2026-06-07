package org.enveloping.ecobin.business.service;

import java.util.List;
import java.util.Map;

/**
 * 统计服务——收口所有维度聚合统计查询。
 * 所有方法均受 Spring Security 权限约束（/api/statistics/** → SUPER_ADMIN/TENANT），
 * 租户隔离由 MyBatis-Plus TenantLineInnerInterceptor 自动注入 tenant_id 过滤。
 */
public interface StatisticsService {

    /** 今日概览：投递次数 + 总重量 + 投递人数 */
    Map<String, Object> dashboard();

    /** 设备信息统计：总数（spill/online/smoke 预留 0，待设备状态上报落地后接入） */
    Map<String, Object> deviceStats();

    /** 会员统计：会员总数 + 今日新增 + 禁用数 */
    Map<String, Object> memberStats();

    /** 本月投递统计：次数 + 重量 + 金额 */
    Map<String, Object> deliveryStats();

    /** 本月清运统计：次数 + 重量 */
    Map<String, Object> cleanStats();

    /** 提现支出统计：次数 + 申请总金额 + 成功金额 */
    Map<String, Object> payoutStats();

    /** 会员资金统计：会员数 + 可用余额总和 + 待审核余额总和 */
    Map<String, Object> memberMoneyStats();

    /** 设备地图坐标：返回有经纬度的设备列表（状态字段预留 0） */
    List<Map<String, Object>> devicesMap();

    /** 本月设备投递排行：按重量降序，取前 N */
    List<Map<String, Object>> deviceRanking(int pageSize);
}
