package org.enveloping.ecobin.funds.api.port;

import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.framework.reliability.TrustedOrganizationInboxRef;

public interface WechatPayNotificationPort {

    String PAYMENT_KIND = "WECHAT_PAYMENT_NOTIFICATION";
    String TRANSFER_KIND = "WECHAT_TRANSFER_NOTIFICATION";
    String TRANSFER_AUTHORIZATION_KIND =
            "WECHAT_TRANSFER_AUTHORIZATION_NOTIFICATION";

    TrustedInboxScopeResolver scopeResolver(
            String messageKind,
            String mchid,
            String externalOrderNo);

    boolean apply(
            TrustedOrganizationInboxRef sourceInbox,
            long sourceTaskAttemptId,
            String messageKind,
            int schemaVersion,
            String normalizedPayload);
}
