package org.enveloping.ecobin.business.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;
import org.enveloping.ecobin.business.entity.WithdrawOrder;

import java.math.BigDecimal;

/**
 * 提现申请单 Mapper。
 */
@Mapper
public interface WithdrawOrderMapper extends BaseMapper<WithdrawOrder> {

    /** 提现总次数 */
    @Select("SELECT COUNT(*) FROM biz_withdraw_order")
    long countAll();

    /** 提现申请总金额 */
    @Select("SELECT COALESCE(SUM(amount), 0) FROM biz_withdraw_order")
    BigDecimal sumAmount();

    /** 提现成功金额（status=1 已通过） */
    @Select("SELECT COALESCE(SUM(amount), 0) FROM biz_withdraw_order WHERE status = 1")
    BigDecimal sumApprovedAmount();
}
