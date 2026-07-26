package org.enveloping.ecobin.operations.api.inbox;

/**
 * 可信外部适配器的可靠收件入口。
 */
public interface TrustedInboxPort {

    TrustedInboxReceipt receive(TrustedInboxMessage message);
}
