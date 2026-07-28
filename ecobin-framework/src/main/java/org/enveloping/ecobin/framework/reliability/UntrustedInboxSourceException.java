package org.enveloping.ecobin.framework.reliability;

/**
 * Permanent rejection raised when an authenticated transport identity cannot
 * be mapped to the claimed business scope.
 */
public final class UntrustedInboxSourceException extends RuntimeException {

    public UntrustedInboxSourceException(String message) {
        super(message);
    }
}
