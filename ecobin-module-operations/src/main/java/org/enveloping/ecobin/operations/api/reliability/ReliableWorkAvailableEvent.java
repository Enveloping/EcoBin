package org.enveloping.ecobin.operations.api.reliability;

import java.util.Objects;

/**
 * 数据库事务提交后的进程内加速提示。它不承载任务数据，也不是可靠事实；提示丢失时，
 * 低频恢复扫描仍从数据库继续执行。
 */
public record ReliableWorkAvailableEvent(Kind kind) {

    public ReliableWorkAvailableEvent {
        Objects.requireNonNull(kind, "kind");
    }

    public enum Kind {
        DEVICE_INBOX,
        DEVICE_COMMAND
    }
}
