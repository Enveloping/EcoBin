package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Enforces each channel's process-wide in-flight limit across runner calls.
 */
@Component
public class ReliableTaskInFlightLimiter {

    private final ReliableTaskProperties properties;
    private final Map<ReliableTaskChannel, AtomicInteger> inFlight;

    public ReliableTaskInFlightLimiter(ReliableTaskProperties properties) {
        this.properties = properties;
        EnumMap<ReliableTaskChannel, AtomicInteger> counters =
                new EnumMap<>(ReliableTaskChannel.class);
        for (ReliableTaskChannel channel : ReliableTaskChannel.values()) {
            counters.put(channel, new AtomicInteger());
        }
        this.inFlight = counters;
    }

    public boolean tryAcquire(ReliableTaskChannel channel) {
        Objects.requireNonNull(channel, "channel");
        properties.validate();
        AtomicInteger counter = inFlight.get(channel);
        int maximum = channelProperties(channel).getMaximumInFlight();
        while (true) {
            int current = counter.get();
            if (current >= maximum) {
                return false;
            }
            if (counter.compareAndSet(current, current + 1)) {
                return true;
            }
        }
    }

    public void release(ReliableTaskChannel channel) {
        Objects.requireNonNull(channel, "channel");
        int remaining = inFlight.get(channel).decrementAndGet();
        if (remaining < 0) {
            inFlight.get(channel).incrementAndGet();
            throw new ReliableTaskInvariantException(
                    "released an in-flight permit that was not acquired");
        }
    }

    private ReliableTaskProperties.Channel channelProperties(
            ReliableTaskChannel channel) {
        return switch (channel) {
            case IOT_DEVICE -> properties.getIotDevice();
            case FUNDS_WECHAT -> properties.getFundsWechat();
            case MAINTENANCE -> properties.getMaintenance();
        };
    }
}
