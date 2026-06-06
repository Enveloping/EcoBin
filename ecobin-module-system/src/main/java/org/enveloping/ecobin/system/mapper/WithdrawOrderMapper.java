package org.enveloping.ecobin.system.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.enveloping.ecobin.system.entity.WithdrawOrder;

/**
 * 提现申请单 Mapper。
 */
@Mapper
public interface WithdrawOrderMapper extends BaseMapper<WithdrawOrder> {
}
