package org.enveloping.ecobin.business.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.business.entity.DeliveryOrder;

import java.util.List;
import java.util.Map;

@Mapper
public interface DeliveryOrderMapper extends BaseMapper<DeliveryOrder> {

    /** 统计今日投递次数 */
    @Select("SELECT COUNT(*) FROM biz_delivery_order WHERE DATE(create_time) = CURDATE()")
    long countToday();

    /** 统计今日投递总重量 */
    @Select("SELECT COALESCE(SUM(weight), 0) FROM biz_delivery_order WHERE DATE(create_time) = CURDATE()")
    Double sumTodayWeight();

    /** 统计今日投递人数（去重 user_id） */
    @Select("SELECT COUNT(DISTINCT user_id) FROM biz_delivery_order WHERE DATE(create_time) = CURDATE()")
    long countTodayMembers();

    // ---- 本月投递统计 ----

    /** 本月投递次数 */
    @Select("SELECT COUNT(*) FROM biz_delivery_order WHERE YEAR(create_time) = YEAR(CURDATE()) AND MONTH(create_time) = MONTH(CURDATE())")
    long countMonth();

    /** 本月投递总重量 */
    @Select("SELECT COALESCE(SUM(weight), 0) FROM biz_delivery_order WHERE YEAR(create_time) = YEAR(CURDATE()) AND MONTH(create_time) = MONTH(CURDATE())")
    Double sumMonthWeight();

    /** 本月投递总金额（price × weight，price 为投递时回填的投口单价快照） */
    @Select("SELECT COALESCE(SUM(price * weight), 0) FROM biz_delivery_order WHERE YEAR(create_time) = YEAR(CURDATE()) AND MONTH(create_time) = MONTH(CURDATE())")
    Double sumMonthMoney();

    /** 本月设备投递排行（按重量降序） */
    @Select("SELECT d.id AS device_id, d.name AS device_name, COALESCE(SUM(o.weight), 0) AS total_weight " +
            "FROM biz_delivery_order o LEFT JOIN biz_device d ON o.device_id = d.id " +
            "WHERE YEAR(o.create_time) = YEAR(CURDATE()) AND MONTH(o.create_time) = MONTH(CURDATE()) " +
            "GROUP BY o.device_id, d.name ORDER BY total_weight DESC LIMIT #{pageSize}")
    List<Map<String, Object>> deviceRanking(@Param("pageSize") int pageSize);
}
