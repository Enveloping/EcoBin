package org.enveloping.ecobin.device.application.delivery;

import java.util.Set;

/** Pure monotonic decision for one trusted delivery-start observation. */
final class DeliveryCommandObservationDecision {

    private static final Set<String> TERMINAL = Set.of(
            "BUSINESS_CONFIRMED", "PRE_OPEN_ENDED", "DEVICE_ABORTED");

    private DeliveryCommandObservationDecision() {
    }

    static Action decide(
            String commandType,
            String sessionStatus,
            String stage,
            String errorCode) {
        if (!"START_DELIVERY_SESSION".equals(commandType)
                || TERMINAL.contains(sessionStatus)) {
            return Action.NONE;
        }
        return switch (stage) {
            case "RECEIVED" -> Action.NONE;
            case "ACCEPTED" -> Action.MARK_EDGE_ACCEPTED;
            case "MCU_ACCEPTED" -> Set.of(
                    "AUTHORIZATION_QUEUED",
                    "IN_PROGRESS",
                    "RESULT_PENDING_RECOVERY")
                    .contains(sessionStatus)
                    ? Action.MARK_IN_PROGRESS : Action.NONE;
            case "REJECTED", "PRE_START_FAILED" ->
                    "AUTHORIZATION_QUEUED".equals(sessionStatus)
                            ? Action.END_BEFORE_OPEN : Action.NONE;
            case "FAILED" -> "EDGE_RESTARTED".equals(errorCode)
                    || "RESULT_PENDING_RECOVERY".equals(sessionStatus)
                    ? Action.NONE : Action.REQUIRE_RECOVERY;
            default -> throw new IllegalArgumentException(
                    "unsupported delivery command observation stage");
        };
    }

    enum Action {
        MARK_EDGE_ACCEPTED,
        MARK_IN_PROGRESS,
        END_BEFORE_OPEN,
        REQUIRE_RECOVERY,
        NONE
    }
}
