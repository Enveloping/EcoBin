package org.enveloping.ecobin.framework.reliability;

import java.util.Objects;
import java.util.UUID;

/**
 * 可信迟到事实对原可靠任务发起的重新核对请求。
 */
public record ReliableTaskWake(UUID taskUid, String evidenceKind) {

    public ReliableTaskWake {
        Objects.requireNonNull(taskUid, "taskUid");
        Objects.requireNonNull(evidenceKind, "evidenceKind");
        if (!evidenceKind.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "evidenceKind must be an uppercase stable code");
        }
    }
}
