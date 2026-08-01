package org.enveloping.ecobin.recycling.infrastructure.registration;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 新机构自动发布的第一版无审核清运规则。
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.recycling.default-clean-rule")
public final class DefaultOrganizationCleanRuleProperties {

    private int operationTimeoutSeconds = 1_800;

    public int getOperationTimeoutSeconds() {
        return operationTimeoutSeconds;
    }

    public void setOperationTimeoutSeconds(
            int operationTimeoutSeconds) {
        this.operationTimeoutSeconds = operationTimeoutSeconds;
    }

    void validate() {
        if (operationTimeoutSeconds != 1_800) {
            throw new IllegalStateException(
                    "default clean operation timeout must be 1800 seconds");
        }
    }
}
