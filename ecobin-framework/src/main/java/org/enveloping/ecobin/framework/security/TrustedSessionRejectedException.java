package org.enveloping.ecobin.framework.security;

public class TrustedSessionRejectedException extends RuntimeException {

    public TrustedSessionRejectedException(String message) {
        super(message);
    }
}
