package org.enveloping.ecobin.integration.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 外部适配器运行模式。开发和自动测试默认只能使用 FAKE。
 */
@Component
@ConfigurationProperties(prefix = "ecobin.external")
public class ExternalAdapterModeProperties {

    private Mode mode = Mode.FAKE;
    private final Fake fake = new Fake();

    public Mode getMode() {
        return mode;
    }

    public void setMode(Mode mode) {
        this.mode = mode;
    }

    public Fake getFake() {
        return fake;
    }

    public enum Mode {
        FAKE,
        REAL
    }

    public static final class Fake {

        private boolean blockInbound = true;

        public boolean isBlockInbound() {
            return blockInbound;
        }

        public void setBlockInbound(boolean blockInbound) {
            this.blockInbound = blockInbound;
        }
    }
}
