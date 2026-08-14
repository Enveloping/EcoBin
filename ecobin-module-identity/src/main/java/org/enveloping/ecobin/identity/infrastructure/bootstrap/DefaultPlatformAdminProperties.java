package org.enveloping.ecobin.identity.infrastructure.bootstrap;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * Empty-directory bootstrap controls. The password must come from an
 * out-of-repository secret and is consulted only when the table is empty.
 */
@Component
@ConfigurationProperties(prefix = "ecobin.identity.default-platform-admin")
public final class DefaultPlatformAdminProperties {

    private boolean enabled;
    private String password = "";

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }
}
