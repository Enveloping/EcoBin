package org.enveloping.ecobin.device.application.enrollment;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

import java.time.Duration;

@Component
@ConfigurationProperties(prefix = "ecobin.device.enrollment")
public class DeviceEnrollmentProperties {

    private boolean enabled;
    private String keyId = "K1";
    private String keyBase64;
    private Duration challengeLifetime = Duration.ofMinutes(10);
    private Duration retryDelay = Duration.ofSeconds(10);
    private Duration provisioningTimeout = Duration.ofMinutes(2);
    private int challengeLimitPerMinute = 20;
    private String modelCode = "EC-M0";
    private int expectedPortCount = 1;
    private String mqttHost = "studio-mqtt.heclouds.com";
    private int mqttPort = 1883;

    public boolean isEnabled() {
        return enabled;
    }

    public void setEnabled(boolean enabled) {
        this.enabled = enabled;
    }

    public String getKeyId() {
        return keyId;
    }

    public void setKeyId(String keyId) {
        this.keyId = keyId;
    }

    public String getKeyBase64() {
        return keyBase64;
    }

    public void setKeyBase64(String keyBase64) {
        this.keyBase64 = keyBase64;
    }

    public Duration getChallengeLifetime() {
        return challengeLifetime;
    }

    public void setChallengeLifetime(Duration challengeLifetime) {
        this.challengeLifetime = challengeLifetime;
    }

    public Duration getRetryDelay() {
        return retryDelay;
    }

    public void setRetryDelay(Duration retryDelay) {
        this.retryDelay = retryDelay;
    }

    public Duration getProvisioningTimeout() {
        return provisioningTimeout;
    }

    public void setProvisioningTimeout(Duration provisioningTimeout) {
        this.provisioningTimeout = provisioningTimeout;
    }

    public int getChallengeLimitPerMinute() {
        return challengeLimitPerMinute;
    }

    public void setChallengeLimitPerMinute(int challengeLimitPerMinute) {
        this.challengeLimitPerMinute = challengeLimitPerMinute;
    }

    public String getModelCode() {
        return modelCode;
    }

    public void setModelCode(String modelCode) {
        this.modelCode = modelCode;
    }

    public int getExpectedPortCount() {
        return expectedPortCount;
    }

    public void setExpectedPortCount(int expectedPortCount) {
        this.expectedPortCount = expectedPortCount;
    }

    public String getMqttHost() {
        return mqttHost;
    }

    public void setMqttHost(String mqttHost) {
        this.mqttHost = mqttHost;
    }

    public int getMqttPort() {
        return mqttPort;
    }

    public void setMqttPort(int mqttPort) {
        this.mqttPort = mqttPort;
    }
}
