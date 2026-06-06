package org.enveloping.ecobin.system.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;
import org.enveloping.ecobin.system.entity.User;

import java.math.BigDecimal;

/**
 * 用户 Mapper（仅保留 BaseMapper 无法覆盖的自定义查询）
 */
@Mapper
public interface UserMapper extends BaseMapper<User> {

    /** 按用户名查询（登录用） */
    @Select("SELECT * FROM sys_user WHERE username = #{username}")
    User selectByUsername(@Param("username") String username);

    /** 按租户 + openid 查询（同一 openid 在不同租户下独立注册） */
    @Select("SELECT * FROM sys_user WHERE tenant_id = #{tenantId} AND openid = #{openid}")
    User selectByTenantIdAndOpenid(@Param("tenantId") Long tenantId, @Param("openid") String openid);

    // ---- 余额原子操作（按主键 id 定位，userId 由登录态/提现单确定，不存在跨用户越权；
    //      租户拦截器仍会按当前上下文追加 tenant_id 过滤——登录态下强化为本租户隔离，
    //      IoT 入账在忽略租户上下文下按 id 全局更新） ----

    /** 投递返现入账：增加可用余额 */
    @Update("UPDATE sys_user SET balance = balance + #{amount} WHERE id = #{userId}")
    int addBalance(@Param("userId") Long userId, @Param("amount") BigDecimal amount);

    /** 提现冻结：可用→待审核；条件保证不透支，返回 0 表示余额不足 */
    @Update("UPDATE sys_user SET balance = balance - #{amount}, pending_balance = pending_balance + #{amount} " +
            "WHERE id = #{userId} AND balance >= #{amount}")
    int freezeForWithdraw(@Param("userId") Long userId, @Param("amount") BigDecimal amount);

    /** 提现通过：扣减待审核（资金转出） */
    @Update("UPDATE sys_user SET pending_balance = pending_balance - #{amount} WHERE id = #{userId}")
    int settlePending(@Param("userId") Long userId, @Param("amount") BigDecimal amount);

    /** 提现驳回：待审核→退回可用 */
    @Update("UPDATE sys_user SET pending_balance = pending_balance - #{amount}, balance = balance + #{amount} " +
            "WHERE id = #{userId}")
    int refundPending(@Param("userId") Long userId, @Param("amount") BigDecimal amount);
}
