package org.enveloping.ecobin.framework.observability;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 本地真实渠道联调时使用的详细诊断日志开关。
 *
 * <p>默认关闭，由 {@code application-local-real.yml} 显式开启。生产或普通
 * Fake 运行不会因为这些属性而记录外部报文或逐条 SQL。</p>
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.observability.diagnostic-logging")
public final class DiagnosticLoggingProperties {

    private final OneNet oneNet = new OneNet();
    private final Sql sql = new Sql();

    public OneNet getOneNet() {
        return oneNet;
    }

    public Sql getSql() {
        return sql;
    }

    public static final class OneNet {

        private boolean enabled;
        private boolean includePayload = true;
        private boolean includeStackTrace = true;
        private int maxPayloadLength = 32_768;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public boolean isIncludePayload() {
            return includePayload;
        }

        public void setIncludePayload(boolean includePayload) {
            this.includePayload = includePayload;
        }

        public boolean isIncludeStackTrace() {
            return includeStackTrace;
        }

        public void setIncludeStackTrace(boolean includeStackTrace) {
            this.includeStackTrace = includeStackTrace;
        }

        public int getMaxPayloadLength() {
            if (maxPayloadLength < 1_024
                    || maxPayloadLength > 262_144) {
                throw new IllegalStateException(
                        "OneNet diagnostic payload length must be between 1024 and 262144 characters");
            }
            return maxPayloadLength;
        }

        public void setMaxPayloadLength(int maxPayloadLength) {
            this.maxPayloadLength = maxPayloadLength;
        }
    }

    public static final class Sql {

        private boolean enabled;
        private boolean includeParameterTypes = true;
        private boolean includeStackTrace = true;
        private int maxSqlLength = 4_096;
        private long slowQueryThresholdMs = 200;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public boolean isIncludeParameterTypes() {
            return includeParameterTypes;
        }

        public void setIncludeParameterTypes(
                boolean includeParameterTypes) {
            this.includeParameterTypes = includeParameterTypes;
        }

        public boolean isIncludeStackTrace() {
            return includeStackTrace;
        }

        public void setIncludeStackTrace(boolean includeStackTrace) {
            this.includeStackTrace = includeStackTrace;
        }

        public int getMaxSqlLength() {
            if (maxSqlLength < 256 || maxSqlLength > 65_536) {
                throw new IllegalStateException(
                        "SQL diagnostic length must be between 256 and 65536 characters");
            }
            return maxSqlLength;
        }

        public void setMaxSqlLength(int maxSqlLength) {
            this.maxSqlLength = maxSqlLength;
        }

        public long getSlowQueryThresholdMs() {
            if (slowQueryThresholdMs < 1
                    || slowQueryThresholdMs > 60_000) {
                throw new IllegalStateException(
                        "SQL slow query threshold must be between 1 and 60000 milliseconds");
            }
            return slowQueryThresholdMs;
        }

        public void setSlowQueryThresholdMs(
                long slowQueryThresholdMs) {
            this.slowQueryThresholdMs = slowQueryThresholdMs;
        }
    }
}
