package org.enveloping.ecobin.business.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.business.entity.CleanOrder;

@Mapper
public interface CleanOrderMapper extends BaseMapper<CleanOrder> {

    // ---- 今日清运统计 ----

    /** 今日清运次数 */
    @Select("SELECT COUNT(*) FROM biz_clean_order WHERE DATE(create_time) = CURDATE()")
    long countToday();

    /** 今日清运总重量 */
    @Select("SELECT COALESCE(SUM(weight), 0) FROM biz_clean_order WHERE DATE(create_time) = CURDATE()")
    Double sumTodayWeight();

    // ---- 本月清运统计 ----

    /** 本月清运次数 */
    @Select("SELECT COUNT(*) FROM biz_clean_order WHERE YEAR(create_time) = YEAR(CURDATE()) AND MONTH(create_time) = MONTH(CURDATE())")
    long countMonth();

    /** 本月清运总重量 */
    @Select("SELECT COALESCE(SUM(weight), 0) FROM biz_clean_order WHERE YEAR(create_time) = YEAR(CURDATE()) AND MONTH(create_time) = MONTH(CURDATE())")
    Double sumMonthWeight();
}
