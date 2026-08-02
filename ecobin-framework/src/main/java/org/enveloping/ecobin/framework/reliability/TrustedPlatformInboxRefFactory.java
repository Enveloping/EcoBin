package org.enveloping.ecobin.framework.reliability;

public interface TrustedPlatformInboxRefFactory {

    TrustedPlatformInboxRef issue(long inboxKey);
}
