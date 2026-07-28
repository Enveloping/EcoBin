package org.enveloping.ecobin.integration.onenet.inbound;

/**
 * Poison-message marker. The MQ Adapter acknowledges these messages after
 * safe rejection so they cannot starve the subscription.
 */
public final class OneNetPermanentMessageException
        extends RuntimeException {

    public OneNetPermanentMessageException(String message) {
        super(message);
    }

    public OneNetPermanentMessageException(
            String message, Throwable cause) {
        super(message, cause);
    }
}
