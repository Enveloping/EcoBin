package org.enveloping.ecobin.integration.config;

/**
 * 外部适配器配置可能越过 Fake/真实环境边界。
 */
public final class ExternalBoundaryException extends IllegalStateException {

    public ExternalBoundaryException(String message) {
        super(message);
    }
}
