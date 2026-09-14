package org.enveloping.ecobin.recycling.application.device;

import java.util.Set;

/** Pure monotonic decision for one trusted cleaning command observation. */
final class CleanCommandObservationDecision {

    private static final Set<String> TERMINAL = Set.of(
            "PRE_UNLOCK_ENDED", "COMPLETED", "ABORTED");
    private static final Set<String> TERMINAL_RESULT_FAILURES = Set.of(
            "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
            "MCU_WORK_CANCELLED",
            "MCU_WORK_FAILED");

    private CleanCommandObservationDecision() {
    }

    static Action decide(
            String commandType,
            String operationStatus,
            boolean firstUnlockMayHaveExecuted,
            String stage,
            String errorCode) {
        if (!"START_CLEAN_OPERATION".equals(commandType)
                || TERMINAL.contains(operationStatus)) {
            return Action.NONE;
        }
        return switch (stage) {
            case "RECEIVED" -> Action.NONE;
            case "ACCEPTED" -> Action.MARK_EDGE_SAVED;
            case "MCU_ACCEPTED" -> Action.MARK_IN_PROGRESS;
            case "REJECTED", "PRE_START_FAILED" ->
                    firstUnlockMayHaveExecuted
                            ? Action.NONE
                            : Action.END_BEFORE_UNLOCK;
            case "FAILED" -> {
                if ("EDGE_RESTARTED".equals(errorCode)) {
                    yield Action.NONE;
                }
                if ("MCU_INITIAL_WEIGHT_UNAVAILABLE".equals(errorCode)) {
                    yield Action.END_PROVEN_BEFORE_UNLOCK;
                }
                if (TERMINAL_RESULT_FAILURES.contains(errorCode)) {
                    yield Action.ABORT_TERMINAL_RESULT;
                }
                yield "RECOVERY_REQUIRED".equals(operationStatus)
                        ? Action.NONE : Action.REQUIRE_RECOVERY;
            }
            default -> throw new IllegalArgumentException(
                    "unsupported clean command observation stage");
        };
    }

    enum Action {
        MARK_EDGE_SAVED,
        MARK_IN_PROGRESS,
        END_BEFORE_UNLOCK,
        END_PROVEN_BEFORE_UNLOCK,
        ABORT_TERMINAL_RESULT,
        REQUIRE_RECOVERY,
        NONE
    }
}
