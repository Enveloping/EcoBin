package org.enveloping.ecobin.business.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.business.entity.DeliveryOrder;

@Mapper
public interface DeliveryOrderMapper extends BaseMapper<DeliveryOrder> {

    /** 统计今日投递次数 */
    @Select("SELECT COUNT(*) FROM biz_delivery_order WHERE DATE(create_time) = CURDATE()")
    long countToday();

    /** 统计今日投递总重量 */
    @Select("SELECT COALESCE(SUM(weight), 0) FROM biz_delivery_order WHERE DATE(create_time) = CURDATE()")
    Double sumTodayWeight();
}
