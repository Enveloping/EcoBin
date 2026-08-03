package org.enveloping.ecobin.funds.api.port;

import java.util.Objects;
import java.util.UUID;

/** operations worker 调用 funds 领域任务的分派边界。 */
public interface ReliableFundsTaskExecutorPort {

    Result execute(Command command);

    record Command(
            UUID taskUid,
            UUID attemptUid,
            long sourceTaskAttemptId,
            String taskType,
            String targetStableKey) {

        public Command {
            Objects.requireNonNull(taskUid, "taskUid");
            Objects.requireNonNull(attemptUid, "attemptUid");
            if (sourceTaskAttemptId <= 0) {
                throw new IllegalArgumentException(
                        "sourceTaskAttemptId must be positive");
            }
            Objects.requireNonNull(taskType, "taskType");
            Objects.requireNonNull(targetStableKey, "targetStableKey");
        }
    }

    record Result(Outcome outcome, String diagnostic) {

        public Result {
            Objects.requireNonNull(outcome, "outcome");
        }

        public enum Outcome {
            DONE,
            RETRY,
            BLOCKED,
            WAITING
        }
    }
}
