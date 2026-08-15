package org.enveloping.ecobin.device.api.port;

/**
 * A sanitized OneNet provisioning failure.  It deliberately carries no
 * request or response body because those envelopes can contain a device key.
 */
public final class OneNetDeviceProvisioningException
        extends RuntimeException {

    private final String code;
    private final boolean retryable;

    private OneNetDeviceProvisioningException(
            String code,
            String message,
            boolean retryable,
            Throwable cause) {
        super(message, cause);
        if (code == null || !code.matches("[A-Z0-9_]{1,100}")) {
            throw new IllegalArgumentException("invalid provisioning code");
        }
        this.code = code;
        this.retryable = retryable;
    }

    public static OneNetDeviceProvisioningException retryable(
            String code,
            String message,
            Throwable cause) {
        return new OneNetDeviceProvisioningException(
                code, message, true, cause);
    }

    public static OneNetDeviceProvisioningException permanent(
            String code,
            String message) {
        return new OneNetDeviceProvisioningException(
                code, message, false, null);
    }

    public String code() {
        return code;
    }

    public boolean retryable() {
        return retryable;
    }
}
