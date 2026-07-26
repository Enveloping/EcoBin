package org.enveloping.ecobin.bootstrap.database.epoch;

/**
 * 目标数据库纪元不满足运行门槛。
 */
public final class DatabaseEpochException extends IllegalStateException {

    public DatabaseEpochException(String message) {
        super(message);
    }

    public DatabaseEpochException(String message, Throwable cause) {
        super(message, cause);
    }
}
