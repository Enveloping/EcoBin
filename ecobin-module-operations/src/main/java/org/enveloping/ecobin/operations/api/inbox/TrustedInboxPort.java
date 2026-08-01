package org.enveloping.ecobin.operations.api.inbox;

import java.util.UUID;

/**
 * 可信外部适配器的可靠收件入口。
 */
public interface TrustedInboxPort {

    TrustedInboxReceipt receive(TrustedInboxMessage message);

    UUID quarantine(TrustedInboxRejection rejection);
}
