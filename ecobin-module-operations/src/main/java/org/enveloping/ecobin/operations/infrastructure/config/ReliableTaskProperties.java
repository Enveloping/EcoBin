package org.enveloping.ecobin.operations.infrastructure.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;
import java.util.Objects;

/**
 * 可靠执行器的全部容量和时间边界。默认值可用于 Fake tracer，生产仍应显式配置。
 */
@ConfigurationProperties(prefix = "ecobin.operations.reliable")
public class ReliableTaskProperties {

    private boolean workersEnabled = true;
    private Channel iotDevice = Channel.iotDeviceDefaults();
    private Channel fundsWechat = Channel.fundsWechatDefaults();
    private Channel maintenance = Channel.maintenanceDefaults();

    public boolean isWorkersEnabled() {
        return workersEnabled;
    }

    public void setWorkersEnabled(boolean workersEnabled) {
        this.workersEnabled = workersEnabled;
    }

    public Channel getIotDevice() {
        return iotDevice;
    }

    public void setIotDevice(Channel iotDevice) {
        this.iotDevice = Objects.requireNonNull(iotDevice, "iotDevice");
    }

    public Channel getFundsWechat() {
        return fundsWechat;
    }

    public void setFundsWechat(Channel fundsWechat) {
        this.fundsWechat = Objects.requireNonNull(fundsWechat, "fundsWechat");
    }

    public Channel getMaintenance() {
        return maintenance;
    }

    public void setMaintenance(Channel maintenance) {
        this.maintenance = Objects.requireNonNull(maintenance, "maintenance");
    }

    public void validate() {
        iotDevice.validate("iot-device");
        fundsWechat.validate("funds-wechat");
        maintenance.validate("maintenance");
    }

    public static class Channel {

        private int workerCount;
        private int batchSize;
        private Duration leaseDuration;
        private Duration externalTimeout;
        private int maximumInFlight;
        private int boundedQueueCapacity;
        private Duration pollInterval;
        private Duration initialBackoff;
        private Duration maximumBackoff;
        private int maxAutoAttempts;

        public static Channel iotDeviceDefaults() {
            return new Channel(
                    2, 16, Duration.ofSeconds(30), Duration.ofSeconds(10),
                    16, 64, Duration.ofMillis(500), Duration.ofSeconds(1),
                    Duration.ofMinutes(5), 10);
        }

        public static Channel fundsWechatDefaults() {
            return new Channel(
                    2, 8, Duration.ofSeconds(60), Duration.ofSeconds(30),
                    8, 32, Duration.ofSeconds(1), Duration.ofSeconds(2),
                    Duration.ofMinutes(10), 20);
        }

        public static Channel maintenanceDefaults() {
            return new Channel(
                    1, 4, Duration.ofSeconds(60), Duration.ofSeconds(20),
                    4, 16, Duration.ofSeconds(5), Duration.ofSeconds(5),
                    Duration.ofMinutes(30), 10);
        }

        public Channel() {
        }

        private Channel(
                int workerCount,
                int batchSize,
                Duration leaseDuration,
                Duration externalTimeout,
                int maximumInFlight,
                int boundedQueueCapacity,
                Duration pollInterval,
                Duration initialBackoff,
                Duration maximumBackoff,
                int maxAutoAttempts) {
            this.workerCount = workerCount;
            this.batchSize = batchSize;
            this.leaseDuration = leaseDuration;
            this.externalTimeout = externalTimeout;
            this.maximumInFlight = maximumInFlight;
            this.boundedQueueCapacity = boundedQueueCapacity;
            this.pollInterval = pollInterval;
            this.initialBackoff = initialBackoff;
            this.maximumBackoff = maximumBackoff;
            this.maxAutoAttempts = maxAutoAttempts;
        }

        public void validate(String name) {
            requireRange(workerCount, 1, 32, name + ".workerCount");
            requireRange(batchSize, 1, 256, name + ".batchSize");
            requireRange(maximumInFlight, 1, 1024, name + ".maximumInFlight");
            requireRange(
                    boundedQueueCapacity,
                    maximumInFlight,
                    4096,
                    name + ".boundedQueueCapacity");
            requireRange(maxAutoAttempts, 1, 1000, name + ".maxAutoAttempts");
            requirePositive(leaseDuration, name + ".leaseDuration");
            requirePositive(externalTimeout, name + ".externalTimeout");
            requirePositive(pollInterval, name + ".pollInterval");
            requirePositive(initialBackoff, name + ".initialBackoff");
            requirePositive(maximumBackoff, name + ".maximumBackoff");
            if (leaseDuration.compareTo(externalTimeout) <= 0) {
                throw new IllegalStateException(
                        name + ".leaseDuration must exceed externalTimeout");
            }
            if (maximumBackoff.compareTo(initialBackoff) < 0) {
                throw new IllegalStateException(
                        name + ".maximumBackoff must not be below initialBackoff");
            }
        }

        private static void requireRange(
                int value, int minimum, int maximum, String field) {
            if (value < minimum || value > maximum) {
                throw new IllegalStateException(
                        field + " must be between " + minimum + " and " + maximum);
            }
        }

        private static void requirePositive(Duration value, String field) {
            if (value == null || value.isZero() || value.isNegative()) {
                throw new IllegalStateException(field + " must be positive");
            }
        }

        public int getWorkerCount() {
            return workerCount;
        }

        public void setWorkerCount(int workerCount) {
            this.workerCount = workerCount;
        }

        public int getBatchSize() {
            return batchSize;
        }

        public void setBatchSize(int batchSize) {
            this.batchSize = batchSize;
        }

        public Duration getLeaseDuration() {
            return leaseDuration;
        }

        public void setLeaseDuration(Duration leaseDuration) {
            this.leaseDuration = leaseDuration;
        }

        public Duration getExternalTimeout() {
            return externalTimeout;
        }

        public void setExternalTimeout(Duration externalTimeout) {
            this.externalTimeout = externalTimeout;
        }

        public int getMaximumInFlight() {
            return maximumInFlight;
        }

        public void setMaximumInFlight(int maximumInFlight) {
            this.maximumInFlight = maximumInFlight;
        }

        public int getBoundedQueueCapacity() {
            return boundedQueueCapacity;
        }

        public void setBoundedQueueCapacity(int boundedQueueCapacity) {
            this.boundedQueueCapacity = boundedQueueCapacity;
        }

        public Duration getPollInterval() {
            return pollInterval;
        }

        public void setPollInterval(Duration pollInterval) {
            this.pollInterval = pollInterval;
        }

        public Duration getInitialBackoff() {
            return initialBackoff;
        }

        public void setInitialBackoff(Duration initialBackoff) {
            this.initialBackoff = initialBackoff;
        }

        public Duration getMaximumBackoff() {
            return maximumBackoff;
        }

        public void setMaximumBackoff(Duration maximumBackoff) {
            this.maximumBackoff = maximumBackoff;
        }

        public int getMaxAutoAttempts() {
            return maxAutoAttempts;
        }

        public void setMaxAutoAttempts(int maxAutoAttempts) {
            this.maxAutoAttempts = maxAutoAttempts;
        }
    }
}
