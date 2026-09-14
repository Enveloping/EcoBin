package org.enveloping.ecobin.device.application.delivery;

import java.util.Set;

/** Pure monotonic decision for one trusted delivery-start observation. */
final class DeliveryCommandObservationDecision {

    private static final Set<String> TERMINAL = Set.of(
            "BUSINESS_CONFIRMED", "PRE_OPEN_ENDED", "DEVICE_ABORTED");
    private static final Set<String> TERMINAL_RESULT_FAILURES = Set.of(
            "MCU_INITIAL_WEIGHT_UNAVAILABLE",
            "MCU_WORK_CANCELLED",
            "MCU_WORK_FAILED");
    // This code is emitted only by the UART v2 NativeBusinessRuntime after it
    // has durably failed the original permit. Fixed-frame transport failures
    // retain their existing UART_* reasons and historical quarantine path.
    private static final String NATIVE_CONTROL_COMMUNICATION_FAILURE =
            "MCU_COMMUNICATION_UNAVAILABLE";

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
            case "FAILED" -> {
                if ("EDGE_RESTARTED".equals(errorCode)) {
                    yield Action.NONE;
                }
                if (TERMINAL_RESULT_FAILURES.contains(errorCode)) {
                    yield Action.ABORT_TERMINAL_RESULT;
                }
                if (NATIVE_CONTROL_COMMUNICATION_FAILURE.equals(errorCode)) {
                    yield Action.ABORT_NATIVE_CONTROL_FAILURE;
                }
                yield "RESULT_PENDING_RECOVERY".equals(sessionStatus)
                        ? Action.NONE : Action.REQUIRE_RECOVERY;
            }
            default -> throw new IllegalArgumentException(
                    "unsupported delivery command observation stage");
        };
    }

    enum Action {
        MARK_EDGE_ACCEPTED,
        MARK_IN_PROGRESS,
        END_BEFORE_OPEN,
        ABORT_TERMINAL_RESULT,
        ABORT_NATIVE_CONTROL_FAILURE,
        REQUIRE_RECOVERY,
        NONE
    }
}
