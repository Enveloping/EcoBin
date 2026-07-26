package org.enveloping.ecobin.bootstrap.database.epoch;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 仅供旧 H2 快速测试使用的显式旁路。
 *
 * <p>旁路同时要求 {@code test} profile 和内存 H2；MySQL、普通运行 profile
 * 或打包后的 Fake 环境都不能借此跳过目标纪元校验。</p>
 */
@Component
@ConfigurationProperties(prefix = "ecobin.database.epoch")
public class DatabaseEpochGuardProperties {

    private boolean testBypass;

    public boolean isTestBypass() {
        return testBypass;
    }

    public void setTestBypass(boolean testBypass) {
        this.testBypass = testBypass;
    }
}
