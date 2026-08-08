package org.enveloping.ecobin.recycling.infrastructure.bag;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.Map;

/** Versioned HMAC keys used only to issue and authenticate physical labels. */
@Component
@ConfigurationProperties(prefix = "ecobin.recycling.bag-code")
public final class BagCodeAuthenticationProperties {

    private String activeKeyId = "K1";
    private Map<String, String> keys = new LinkedHashMap<>();

    public String getActiveKeyId() {
        return activeKeyId;
    }

    public void setActiveKeyId(String activeKeyId) {
        this.activeKeyId = activeKeyId;
    }

    public Map<String, String> getKeys() {
        return keys;
    }

    public void setKeys(Map<String, String> keys) {
        this.keys = keys == null
                ? new LinkedHashMap<>()
                : new LinkedHashMap<>(keys);
    }
}
