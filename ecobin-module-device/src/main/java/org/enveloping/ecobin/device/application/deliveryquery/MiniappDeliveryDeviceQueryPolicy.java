package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.value.DeviceRuntimeWeightPolicy;

import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

final class MiniappDeliveryDeviceQueryPolicy {

    static final String DEPLOYMENT_NOT_ENABLED =
            "DEPLOYMENT_NOT_ENABLED";
    static final String BUSINESS_SWITCH_DISABLED =
            "BUSINESS_SWITCH_DISABLED";
    static final String CONFIGURATION_NOT_APPLIED =
            "CONFIGURATION_NOT_APPLIED";
    static final String EDGE_OFFLINE = "EDGE_OFFLINE";
    static final String SAFETY_LOCKED = "SAFETY_LOCKED";
    static final String PORT_DISABLED = "PORT_DISABLED";
    static final String PORT_SENSOR_UNHEALTHY =
            "PORT_SENSOR_UNHEALTHY";
    static final String DELIVERY_RESULT_PENDING =
            "DELIVERY_RESULT_PENDING";
    static final String DEVICE_BUSY = "DEVICE_BUSY";

    private static final Set<String> KNOWN_ACTUATOR_FAILURES = Set.of(
            "TIMEOUT",
            "ACTUATOR_FAULT",
            "SWITCH_FAULT",
            "DISCONNECTED");

    private MiniappDeliveryDeviceQueryPolicy() {
    }

    static Evaluation evaluate(
            MiniappDeliveryDeviceQueryRepository.DeploymentSnapshotRow
                    deployment,
            List<MiniappDeliveryDeviceQueryRepository.PortSnapshotRow>
                    ports,
            LocalDateTime now) {
        boolean exactConfiguration =
                exactConfiguration(deployment);
        LinkedHashSet<String> common = new LinkedHashSet<>();
        if (!"IN_USE".equals(deployment.assetLifecycleStatus())
                || !"ENABLED".equals(
                        deployment.deploymentLifecycleStatus())) {
            common.add(DEPLOYMENT_NOT_ENABLED);
        }
        if (!deployment.businessEnabled()) {
            common.add(BUSINESS_SWITCH_DISABLED);
        }
        if (!exactConfiguration) {
            common.add(CONFIGURATION_NOT_APPLIED);
        }
        if (!trustedRuntimeAvailable(deployment)) {
            common.add(EDGE_OFFLINE);
        }
        if (!"HEALTHY".equals(deployment.localStorageState())
                || !"OK".equals(
                        deployment.localStorageHealth())
                || !"SAFE".equals(deployment.safetyStatus())) {
            common.add(SAFETY_LOCKED);
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

        boolean sameTrustedSnapshot =
                port.trustedRuntimeEdgeEventId() != null
                        && "DEVICE_RUNTIME_SNAPSHOT".equals(
                                port.trustedRuntimeEdgeEventType())
                        && port.trustedRuntimeSequence() != null
                        && port.trustedRuntimeSequence() > 0
                        && Objects.equals(
                                port.trustedRuntimeEdgeEventId(),
                                deployment.trustedRuntimeEdgeEventId())
                        && Objects.equals(
                                port.trustedRuntimeSequence(),
                                deployment.trustedRuntimeSequence());
        boolean weightReady = DeviceRuntimeWeightPolicy.isStartEligible(
                port.weightSensorHealth(),
                port.weightMeasurementStatus(),
                port.weightValueAvailable(),
                port.reportedWeightGrams(),
                port.weightValueKind(),
                port.runtimeCalibrationVersion(),
                port.configuredCalibrationVersion());
        if (!sameTrustedSnapshot || !weightReady) {
            blockers.add(PORT_SENSOR_UNHEALTHY);
        }

        if (port.pendingDeliveryResultSessionId() != null) {
            blockers.add(DELIVERY_RESULT_PENDING);
        }
        boolean unsafe =
                !"SAFE".equals(port.safetyStatus())
                        || !"DEENERGIZED".equals(
                                port.cleanLockPowerState())
                        || !"OK".equals(
                                port.cleanSolenoidHealth())
                        || !"NORMAL".equals(port.smokeState())
                        || !"OK".equals(port.smokeSensorHealth())
                        || port.runtimeFaultBitmap() == null
                        || port.runtimeFaultBitmap() != 0
                        || KNOWN_ACTUATOR_FAILURES.contains(
                                port.deliveryDoorActuatorHealth());
        if (unsafe) {
            blockers.add(SAFETY_LOCKED);
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
            MiniappDeliveryDeviceQueryRepository.DeploymentSnapshotRow
                    deployment) {
        if (deployment.configurationId() == null
                || deployment.configurationVersion() == null) {
            return false;
        }
        boolean applicationExact =
                "APPLIED".equals(
                        deployment.configurationApplicationStatus())
                        && deployment.configurationAppliedAt() != null
                        && Objects.equals(
                                deployment.applicationReportedVersion(),
                                deployment.configurationVersion())
                        && sameDigest(
                                deployment
                                        .applicationReportedContentSha256(),
                                deployment
                                        .configurationContentSha256())
                        && sameDigest(
                                deployment
                                        .applicationReportedMcuPayloadSha256(),
                                deployment
                                        .configurationMcuPayloadSha256());
        boolean orangePiExact =
                Objects.equals(
                        deployment
                                .orangePiReportedConfigurationVersion(),
                        deployment.configurationVersion())
                        && sameDigest(
                                deployment
                                        .orangePiReportedConfigurationContentSha256(),
                                deployment
                                        .configurationContentSha256())
                        && sameDigest(
                                deployment
                                        .orangePiReportedConfigurationMcuPayloadSha256(),
                                deployment
                                        .configurationMcuPayloadSha256());
        return applicationExact && orangePiExact;
    }

    private static boolean trustedRuntimeAvailable(
            MiniappDeliveryDeviceQueryRepository.DeploymentSnapshotRow
                    deployment) {
        return deployment.trustedRuntimeEdgeEventId() != null
                && "DEVICE_RUNTIME_SNAPSHOT".equals(
                        deployment.trustedRuntimeEdgeEventType())
                && deployment.trustedRuntimeSequence() != null
                && deployment.trustedRuntimeSequence() > 0
                && deployment.trustedRuntimeReceivedAt() != null
                && "ONLINE".equals(deployment.edgeConnectionStatus());
    }

    private static boolean sameDigest(byte[] left, byte[] right) {
        return left != null
                && right != null
                && left.length == 32
                && right.length == 32
                && MessageDigest.isEqual(left, right);
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
