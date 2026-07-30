package org.enveloping.ecobin.identity.api.persistence;

/**
 * recycling 在投递审核事务中交给 identity 解析钱包归属用户的请求引用。
 *
 * <p>内部键只能在当前线程、当前事务中由 identity 消费一次；调用方不得把它
 * 序列化、记录或改成普通跨模块主键参数。</p>
 */
public interface DeliveryWalletEntryOwnerRequestRef {

    <T> T withRequestedOwnerOnce(RequestedOwnerFunction<T> function);

    @FunctionalInterface
    interface RequestedOwnerFunction<T> {

        T apply(
                long tenantKey,
                long organizationKey,
                long organizationUserKey);
    }

}
