package org.enveloping.ecobin.recycling.api.port;

import java.time.Duration;
import java.util.Objects;
import java.util.UUID;

/** operations worker 调用 recycling 自动任务的分派边界。 */
public interface ReliableRecyclingTaskExecutorPort {

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

    record Result(
            Outcome outcome,
            String diagnostic,
            Duration retryAfter) {

        public Result {
            Objects.requireNonNull(outcome, "outcome");
            if (retryAfter != null
                    && (retryAfter.isZero() || retryAfter.isNegative())) {
                throw new IllegalArgumentException(
                        "retryAfter must be positive when supplied");
            }
        }

        public Result(Outcome outcome, String diagnostic) {
            this(outcome, diagnostic, null);
        }

        public enum Outcome {
            DONE,
            RETRY,
            BLOCKED,
            WAITING
        }
    }
}
