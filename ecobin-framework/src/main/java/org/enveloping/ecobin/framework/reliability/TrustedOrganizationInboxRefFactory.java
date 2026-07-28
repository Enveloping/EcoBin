package org.enveloping.ecobin.framework.reliability;

public interface TrustedOrganizationInboxRefFactory {

    TrustedOrganizationInboxRef issue(
            long inboxKey,
            long tenantKey,
            long organizationKey);
}
