package org.enveloping.ecobin.device.api.port;

/**
 * Read-only Seam through which the device Module obtains recycling-owned
 * capacity and bag facts without reading recycling tables directly.
 */
public interface DevicePortBusinessSnapshotPort {

    DevicePortBusinessSnapshot find(
            String tenantCode,
            String organizationCode,
            String deviceCode,
            int portNo);

    record DevicePortBusinessSnapshot(
            boolean currentBagPresent,
            String baselineState,
            String detectionGate,
            String fullnessState,
            String displayedFullnessPercent,
            boolean cleanOperationActive) {

        public static DevicePortBusinessSnapshot empty() {
            return new DevicePortBusinessSnapshot(
                    false,
                    "UNINITIALIZED",
                    "UNKNOWN",
                    "UNKNOWN",
                    null,
                    false);
        }
    }
}
