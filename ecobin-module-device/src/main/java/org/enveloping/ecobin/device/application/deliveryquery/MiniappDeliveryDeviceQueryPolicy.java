package org.enveloping.ecobin.device.application.deliveryquery;

import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;

final class MiniappDeliveryDeviceQueryPolicy {

    static final String ASSET_UNAVAILABLE = "ASSET_UNAVAILABLE";
    static final String CONFIGURATION_NOT_APPLIED =
            "CONFIGURATION_NOT_APPLIED";
    static final String EDGE_OFFLINE = "EDGE_OFFLINE";
    static final String PORT_DISABLED = "PORT_DISABLED";
    static final String DEVICE_BUSY = "DEVICE_BUSY";
    static final String DEVICE_SOFTWARE_NOT_ACCEPTING =
            "DEVICE_SOFTWARE_NOT_ACCEPTING";

    private MiniappDeliveryDeviceQueryPolicy() {
    }

    static Evaluation evaluate(
            MiniappDeliveryDeviceQueryRepository.AssetSnapshotRow asset,
            List<MiniappDeliveryDeviceQueryRepository.PortSnapshotRow>
                    ports,
            LocalDateTime now) {
        boolean exactConfiguration = exactConfiguration(asset);
        LinkedHashSet<String> common = new LinkedHashSet<>();
        if (!"NORMAL".equals(asset.lifecycleStatus())
                || !"PASSED".equals(asset.acceptanceStatus())) {
            common.add(ASSET_UNAVAILABLE);
        }
        if (!exactConfiguration) {
            common.add(CONFIGURATION_NOT_APPLIED);
        }
        if (!"ONLINE".equals(asset.edgeConnectionStatus())) {
            common.add(EDGE_OFFLINE);
        }
        if (asset.deviceBusy()) {
            common.add(DEVICE_BUSY);
        }
        if ("PERMANENT_V1".equals(
                asset.managementArchitectureGeneration())
                && !"ACCEPTING".equals(
                asset.businessAdmissionStatus())) {
            common.add(DEVICE_SOFTWARE_NOT_ACCEPTING);
        }

        List<PortEvaluation> evaluatedPorts = ports.stream()
                .map(port -> evaluatePort(
                        port,
                        asset,
                        exactConfiguration,
                        common))
                .toList();
        return new Evaluation(
                exactConfiguration,
                List.copyOf(common),
                evaluatedPorts);
    }

    private static PortEvaluation evaluatePort(
            MiniappDeliveryDeviceQueryRepository.PortSnapshotRow port,
            MiniappDeliveryDeviceQueryRepository.AssetSnapshotRow asset,
            boolean exactConfiguration,
            LinkedHashSet<String> commonBlockers) {
        LinkedHashSet<String> blockers =
                new LinkedHashSet<>(commonBlockers);

        boolean configured =
                Boolean.TRUE.equals(port.businessEnabled())
                && port.unitPriceYuanPerKg() != null
                && port.unitPriceYuanPerKg().signum() > 0
                && port.configuredCalibrationVersion() != null;
        if (!configured) {
            blockers.add(PORT_DISABLED);
        }

        return new PortEvaluation(
                port.portId(),
                port.portNo(),
                exactConfiguration ? port.displayName() : null,
                exactConfiguration
                        && port.unitPriceYuanPerKg() != null
                        ? port.unitPriceYuanPerKg().toPlainString()
                        : null,
                exactConfiguration ? port.fullnessMode() : null,
                List.copyOf(blockers));
    }

    private static boolean exactConfiguration(
            MiniappDeliveryDeviceQueryRepository.AssetSnapshotRow asset) {
        return asset.configurationId() != null
                && asset.configurationVersion() != null
                && "APPLIED".equals(asset.configurationApplicationStatus())
                && java.util.Objects.equals(
                        asset.configurationVersion(),
                        asset.applicationReportedVersion())
                && java.util.Arrays.equals(
                        asset.configurationContentSha256(),
                        asset.applicationReportedContentSha256())
                && java.util.Arrays.equals(
                        asset.configurationMcuPayloadSha256(),
                        asset.applicationReportedMcuPayloadSha256())
                && asset.configurationAppliedAt() != null
                && java.util.Objects.equals(
                        asset.configurationVersion(),
                        asset.progressAppliedConfigurationVersion())
                && java.util.Arrays.equals(
                        asset.configurationContentSha256(),
                        asset.progressAppliedConfigurationContentSha256())
                && java.util.Arrays.equals(
                        asset.configurationMcuPayloadSha256(),
                        asset.progressAppliedConfigurationMcuPayloadSha256());
    }

    record Evaluation(
            boolean exactConfiguration,
            List<String> commonBlockers,
            List<PortEvaluation> ports) {
    }

    record PortEvaluation(
            long portId,
            int portNo,
            String displayName,
            String unitPriceYuanPerKg,
            String fullnessMode,
            List<String> blockers) {
    }
}
