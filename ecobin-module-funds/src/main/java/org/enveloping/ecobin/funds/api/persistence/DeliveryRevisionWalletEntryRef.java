package org.enveloping.ecobin.funds.api.persistence;

import java.util.function.Function;

/**
 * 投递修订与钱包明细之间的关系专用引用。
 *
 * <p>该接口由 recycling 在已经存在的数据库事务中实现并签发。实现必须绑定
 * 签发线程和签发事务、只能消费一次，并且不得在 {@link Object#toString()}
 * 中暴露内部数据库键。funds 只能使用它建立本次钱包明细的复合外键，不能将
 * 内部键保存到日志、任务、缓存、审计或跨事务对象中。</p>
 */
public interface DeliveryRevisionWalletEntryRef {

    <T> T withWalletEntryForeignKeysOnce(
            Function<WalletEntryForeignKeys, T> function);

    record WalletEntryForeignKeys(
            long tenantKey,
            long organizationKey,
            long deliveryRevisionKey) {

        public WalletEntryForeignKeys {
            if (tenantKey <= 0
                    || organizationKey <= 0
                    || deliveryRevisionKey <= 0) {
                throw new IllegalArgumentException(
                        "wallet entry foreign keys must be positive");
            }
        }
    }
}
