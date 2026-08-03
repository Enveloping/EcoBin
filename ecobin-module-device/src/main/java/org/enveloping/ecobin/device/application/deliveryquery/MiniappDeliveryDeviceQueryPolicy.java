package org.enveloping.ecobin.device.application.deliveryquery;

import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;

final class MiniappDeliveryDeviceQueryPolicy {

    static final String DEPLOYMENT_NOT_ENABLED =
            "DEPLOYMENT_NOT_ENABLED";
    static final String BUSINESS_SWITCH_DISABLED =
            "BUSINESS_SWITCH_DISABLED";
    static final String EDGE_OFFLINE = "EDGE_OFFLINE";
    static final String PORT_DISABLED = "PORT_DISABLED";
    static final String DEVICE_BUSY = "DEVICE_BUSY";

    private MiniappDeliveryDeviceQueryPolicy() {
    }

    static Evaluation evaluate(
            MiniappDeliveryDeviceQueryRepository.DeploymentSnapshotRow
                    deployment,
            List<MiniappDeliveryDeviceQueryRepository.PortSnapshotRow>
                    ports,
            LocalDateTime now) {
        boolean exactConfiguration = deployment.configurationId() != null
                && deployment.configurationVersion() != null;
        LinkedHashSet<String> common = new LinkedHashSet<>();
        if (!"IN_USE".equals(deployment.assetLifecycleStatus())
                || !"ENABLED".equals(
                        deployment.deploymentLifecycleStatus())) {
            common.add(DEPLOYMENT_NOT_ENABLED);
        }
        if (!deployment.businessEnabled()) {
            common.add(BUSINESS_SWITCH_DISABLED);
        }
        if (!"ONLINE".equals(deployment.edgeConnectionStatus())) {
            common.add(EDGE_OFFLINE);
        }
        if (deployment.deviceBusy()) {
            common.add(DEVICE_BUSY);
        }

        List<PortEvaluation> evaluatedPorts = ports.stream()
                .map(port -> evaluatePort(
                        port,
                        deployment,
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
            MiniappDeliveryDeviceQueryRepository.DeploymentSnapshotRow
                    deployment,
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
