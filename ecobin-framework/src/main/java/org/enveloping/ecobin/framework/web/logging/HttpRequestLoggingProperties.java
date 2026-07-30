package org.enveloping.ecobin.framework.web.logging;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * HTTP 入口与完成日志配置。
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.observability.http-request-logging")
public final class HttpRequestLoggingProperties {

    private boolean enabled;
    private boolean includeQueryString = true;
    private boolean includeRequestBody;
    private int maxPayloadLength = 4_096;
    private List<String> excludedPathPrefixes =
            new ArrayList<>(List.of("/actuator/health"));

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public boolean isIncludeQueryString() {
        return includeQueryString;
    }

    public void setIncludeQueryString(boolean includeQueryString) {
        this.includeQueryString = includeQueryString;
    }

    public boolean isIncludeRequestBody() {
        return includeRequestBody;
    }

    public void setIncludeRequestBody(boolean includeRequestBody) {
        this.includeRequestBody = includeRequestBody;
    }

    public int getMaxPayloadLength() {
        if (maxPayloadLength < 256 || maxPayloadLength > 65_536) {
            throw new IllegalStateException(
                    "HTTP request log max payload length must be between 256 and 65536 bytes");
        }
        return maxPayloadLength;
    }

    public void setMaxPayloadLength(int maxPayloadLength) {
        this.maxPayloadLength = maxPayloadLength;
    }

    public List<String> getExcludedPathPrefixes() {
        return List.copyOf(excludedPathPrefixes);
    }

    public void setExcludedPathPrefixes(
            List<String> excludedPathPrefixes) {
        this.excludedPathPrefixes = excludedPathPrefixes == null
                ? new ArrayList<>()
                : new ArrayList<>(excludedPathPrefixes);
    }
}
