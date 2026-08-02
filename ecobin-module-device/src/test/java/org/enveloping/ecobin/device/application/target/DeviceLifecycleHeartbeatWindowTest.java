package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class DeviceLifecycleHeartbeatWindowTest {

    @Test
    void extremeHeartbeatValuesSaturateAtOneDayWithoutOverflow() {
        assertEquals(
                86_400_000_000L,
                DeviceLifecycleApplication.heartbeatWindowMicros(
                        4_294_967_295L,
                        2_147_483_647L));
    }

    @Test
    void ordinaryHeartbeatValuesKeepTheirExactWindow() {
        assertEquals(
                90_000_000L,
                DeviceLifecycleApplication.heartbeatWindowMicros(
                        30_000L,
                        3L));
    }
}
