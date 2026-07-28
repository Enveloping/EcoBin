package org.enveloping.ecobin.identity.infrastructure.development;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * Development-only platform administrator bootstrap settings.
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.development.default-platform-admin")
public final class DevelopmentPlatformAdminProperties {

    private boolean enabled = true;
    private String loginName = "admin";
    private String password = "admin123";
    private String displayName = "开发平台管理员";

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getLoginName() {
        return loginName;
    }

    public void setLoginName(String loginName) {
        this.loginName = loginName;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }

    public String getDisplayName() {
        return displayName;
    }

    public void setDisplayName(String displayName) {
        this.displayName = displayName;
    }
}
