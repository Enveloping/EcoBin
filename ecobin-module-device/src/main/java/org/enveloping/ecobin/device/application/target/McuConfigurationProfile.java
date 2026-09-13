package org.enveloping.ecobin.device.application.target;

/** Server-selected, immutable configuration encoding; never an administrator input. */
public enum McuConfigurationProfile {
    LEGACY_V1,
    UART_V2_SIMPLIFIED;

    static final long POLL_INTERVAL_MS = 250;
    static final long RESPONSE_TIMEOUT_MS = 200;
    static final long MAXIMUM_SAMPLE_AGE_MS = 750;
    static final int MINIMUM_MEDIAN_SAMPLE_COUNT = 5;
    static final long MEASUREMENT_TIMEOUT_MS = 5_000;
    static final long STABLE_WINDOW_MS = 1_500;
    static final long MAXIMUM_FLUCTUATION_GRAMS = 100;
    static final int REQUIRED_SAMPLE_COUNT = 5;

    boolean nativeUart() {
        return this == UART_V2_SIMPLIFIED;
    }
}
