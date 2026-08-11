package org.enveloping.ecobin.recycling.application.deliveryquery;

import org.enveloping.ecobin.device.api.port.MiniappDeliveryDeviceQueryPort;
import org.enveloping.ecobin.device.api.query.DeliveryDeviceOptionsQuery;
import org.enveloping.ecobin.device.api.query.OwnedDeliverySessionQuery;
import org.enveloping.ecobin.device.api.result.DeliveryDeviceOptionsSnapshot;
import org.enveloping.ecobin.device.api.result.DeliveryDevicePortOptionSnapshot;
import org.enveloping.ecobin.device.api.result.OwnedDeliverySessionSnapshot;
import org.enveloping.ecobin.funds.api.command.DeliveryWalletQualificationQuery;
import org.enveloping.ecobin.funds.api.port.DeliveryWalletQualificationQueryPort;
import org.enveloping.ecobin.funds.api.result.DeliveryWalletQualification;
import org.enveloping.ecobin.identity.api.port.MiniappDeliveryIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.CurrentMiniappDeliveryIdentity;
import org.enveloping.ecobin.recycling.application.portgeneration.CurrentPortGenerationPolicy;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliveryOptionsView;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliveryPortOption;
import org.enveloping.ecobin.recycling.web.v1.DeliveryModels.DeliverySessionView;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.UUID;

/**
 * 组装小程序投递页需要的只读视图。
 *
 * <p>recycling 在这里协调 identity、funds、device 与回收业务事实，但只做展示；
 * 用户真正开始投递时，写用例仍会按固定锁序重新检查，避免查询后状态发生变化。</p>
 */
@Service
public class MiniappDeliveryQueryService {

    static final long RECOMMENDED_POLL_AFTER_MS = 1_000L;

    static final String PHONE_BINDING_REQUIRED =
            "PHONE_BINDING_REQUIRED";
    static final String CONFIGURATION_NOT_APPLIED =
            "CONFIGURATION_NOT_APPLIED";
    static final String CURRENT_BAG_MISSING =
            "CURRENT_BAG_MISSING";
    static final String PORT_FULL =
            "PORT_FULL";
    static final String WEIGHT_BASELINE_MISSING =
            "WEIGHT_BASELINE_MISSING";
    static final String BASELINE_REMEASUREMENT_ACTIVE =
            "BASELINE_REMEASUREMENT_ACTIVE";
    static final String PORT_CLEAN_OPERATION_ACTIVE =
            "PORT_CLEAN_OPERATION_ACTIVE";
    static final String CLEAN_RESTARTED_CLEAN_REQUIRED =
            "CLEAN_RESTARTED_CLEAN_REQUIRED";

    private final MiniappDeliveryIdentityQueryPort identity;
    private final MiniappDeliveryDeviceQueryPort device;
    private final DeliveryBusinessReadPort business;
    private final DeliveryWalletQualificationQueryPort wallet;

    public MiniappDeliveryQueryService(
            MiniappDeliveryIdentityQueryPort identity,
            MiniappDeliveryDeviceQueryPort device,
            DeliveryBusinessReadPort business,
            DeliveryWalletQualificationQueryPort wallet) {
        this.identity = identity;
        this.device = device;
        this.business = business;
        this.wallet = wallet;
    }

    /**
     * 返回展示快照。这里出现“可投递”不构成设备授权，POST 会在写锁下重复全部准入检查。
     */
    @Transactional(readOnly = true)
    public DeliveryOptionsView deliveryOptions(String deviceCode) {
        CurrentMiniappDeliveryIdentity current = identity.current();
        DeliveryDeviceOptionsSnapshot deviceOptions =
                device.deliveryOptions(
                        new DeliveryDeviceOptionsQuery(
                                deviceCode,
                                current.deliveryQueryUserRef()));
        DeliveryOptionsBusinessFacts businessOptions =
                business.currentOptions(
                        deviceOptions.businessQueryRef());

        LinkedHashSet<String> userBlockers = new LinkedHashSet<>();
        if (!current.phoneBound()) {
            userBlockers.add(PHONE_BINDING_REQUIRED);
        }
        OptionalLong openBalanceFloor =
                businessOptions.openBalanceFloorCent();
        if (openBalanceFloor.isEmpty()) {
            userBlockers.add(CONFIGURATION_NOT_APPLIED);
        } else {
            DeliveryWalletQualification walletQualification =
                    wallet.current(
                            new DeliveryWalletQualificationQuery(
                                    current.organizationUserUid(),
                                    openBalanceFloor.getAsLong(),
                                    current.walletQueryOwnerRef()));
            walletQualification.blockers().forEach(
                    blocker -> userBlockers.add(blocker.name()));
        }

        List<DeliveryPortOption> ports =
                deviceOptions.ports().stream()
                        .map(port -> portOption(
                                port,
                                businessOptions.port(port.portNo()),
                                userBlockers))
                        .toList();
        return new DeliveryOptionsView(
                deviceOptions.deviceCode(),
                deviceOptions.displayName(),
                deviceOptions.address(),
                deviceOptions.deviceBusy(),
                deviceOptions.asOf(),
                ports);
    }

    @Transactional(readOnly = true)
    public DeliverySessionView deliverySession(UUID sessionUid) {
        CurrentMiniappDeliveryIdentity current = identity.current();
        OwnedDeliverySessionSnapshot session =
                device.ownedSession(
                        new OwnedDeliverySessionQuery(
                                sessionUid,
                                current.deliveryQueryUserRef()));
        String deliveryOrderNo =
                business.findDeliveryOrderNo(
                                session.businessQueryRef())
                        .orElse(null);
        SessionPresentation presentation =
                presentSession(session);
        return new DeliverySessionView(
                session.sessionUid(),
                presentation.status(),
                presentation.phase(),
                session.deviceCode(),
                session.portNo(),
                session.firstPhysicalProgressAt(),
                session.endedAt(),
                session.endReason(),
                deliveryOrderNo,
                presentation.recommendedPollAfterMs(),
                presentation.nextActions());
    }

    private static DeliveryPortOption portOption(
            DeliveryDevicePortOptionSnapshot devicePort,
            Optional<DeliveryPortBusinessFacts> businessPort,
            LinkedHashSet<String> userBlockers) {
        LinkedHashSet<String> blockers =
                new LinkedHashSet<>(userBlockers);
        blockers.addAll(devicePort.blockers());
        String fullnessPercent = null;
        if (businessPort.isEmpty()) {
            blockers.add(CURRENT_BAG_MISSING);
        } else {
            DeliveryPortBusinessFacts facts =
                    businessPort.orElseThrow();
            fullnessPercent = decimal(
                    facts.displayedFullnessPercent());
            addBusinessBlockers(
                    blockers,
                    devicePort.fullnessMode(),
                    facts);
        }
        List<String> blockerList = List.copyOf(blockers);
        return new DeliveryPortOption(
                devicePort.portNo(),
                devicePort.displayName(),
                devicePort.unitPriceYuanPerKg(),
                fullnessPercent,
                blockerList.isEmpty(),
                blockerList);
    }

    private static void addBusinessBlockers(
            LinkedHashSet<String> blockers,
            String fullnessMode,
            DeliveryPortBusinessFacts facts) {
        if (!facts.currentBagPresent()) {
            blockers.add(CURRENT_BAG_MISSING);
        }
        if (facts.currentBagPresent()
                && facts.confirmedFullnessState()
                == DeliveryPortBusinessFacts
                .ConfirmedFullnessState.FULL) {
            blockers.add(PORT_FULL);
        }
        // 配置未精确应用时，设备模块按契约隐藏模式；查询只保留阻断，
        // 不能把这个展示态空值传给开始投递使用的严格代际策略。
        if (fullnessMode == null) {
            blockers.add(CONFIGURATION_NOT_APPLIED);
        } else if (facts.currentBagPresent()
                && CurrentPortGenerationPolicy
                .requiresWeightBaseline(fullnessMode)
                && facts.baselineState()
                != DeliveryPortBusinessFacts.BaselineState.VALID) {
            blockers.add(WEIGHT_BASELINE_MISSING);
        }
        if (facts.baselineRemeasurementActive()) {
            blockers.add(BASELINE_REMEASUREMENT_ACTIVE);
        }
        if (facts.cleanOperationActive()) {
            blockers.add(PORT_CLEAN_OPERATION_ACTIVE);
        }
        if (facts.cleanRestartInterlockActive()) {
            blockers.add(CLEAN_RESTARTED_CLEAN_REQUIRED);
        }
    }

    private static SessionPresentation presentSession(
            OwnedDeliverySessionSnapshot session) {
        // 内部状态保留设备/恢复语义；对小程序只暴露用户能理解并采取行动的阶段。
        return switch (session.deviceStatus()) {
            case "PREPARED", "AUTHORIZATION_QUEUED" ->
                    active(
                            "START_QUEUED",
                            "WAIT");
            case "IN_PROGRESS" ->
                    session.deviceCompletedAt() == null
                            ? active(
                            "IN_PROGRESS",
                            "WAIT_ON_DEVICE")
                            : active(
                            "FINAL_RESULT_PENDING",
                            "WAIT");
            case "RESULT_PENDING_RECOVERY" ->
                    active(
                            "RECOVERY_REQUIRED",
                            "WAIT");
            case "BUSINESS_CONFIRMED" ->
                    new SessionPresentation(
                            "COMPLETED",
                            "BUSINESS_CONFIRMED",
                            null,
                            List.of("VIEW_ORDER"));
            case "PRE_OPEN_ENDED" ->
                    new SessionPresentation(
                            "ENDED",
                            "PRE_START_FAILED",
                            null,
                            List.of("SESSION_ENDED"));
            case "DEVICE_ABORTED" ->
                    new SessionPresentation(
                            "ENDED",
                            "DEVICE_RESTART_ABORTED",
                            null,
                            List.of("SESSION_ENDED"));
            default -> throw new IllegalStateException(
                    "unsupported delivery session status");
        };
    }

    private static SessionPresentation active(
            String phase,
            String action) {
        return new SessionPresentation(
                "ACTIVE",
                phase,
                RECOMMENDED_POLL_AFTER_MS,
                List.of(action));
    }

    private static String decimal(BigDecimal value) {
        return value == null ? null : value.toPlainString();
    }

    record SessionPresentation(
            String status,
            String phase,
            Long recommendedPollAfterMs,
            List<String> nextActions) {
    }
}
