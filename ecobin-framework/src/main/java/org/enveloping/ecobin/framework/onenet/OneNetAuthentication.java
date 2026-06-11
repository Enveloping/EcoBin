package org.enveloping.ecobin.framework.onenet;

import org.apache.pulsar.client.api.Authentication;
import org.apache.pulsar.client.api.AuthenticationDataProvider;
import org.apache.pulsar.client.api.EncodedAuthenticationParameterSupport;

import java.io.IOException;
import java.util.Map;

/**
 * OneNet 北向 MQ 的 Pulsar 自定义鉴权（移植官方 SDK demo），鉴权方法名固定为 {@code iot-auth}。
 */
public class OneNetAuthentication implements Authentication, EncodedAuthenticationParameterSupport {

    private static final String METHOD_NAME = "iot-auth";

    private final String accessId;
    private final String secretKey;

    public OneNetAuthentication(String accessId, String secretKey) {
        this.accessId = accessId;
        this.secretKey = secretKey;
    }

    @Override
    public String getAuthMethodName() {
        return METHOD_NAME;
    }

    @Override
    public AuthenticationDataProvider getAuthData() {
        return new OneNetAuthenticationDataProvider(accessId, secretKey);
    }

    @Override
    public void configure(String encodedAuthParamString) {
    }

    @Override
    @Deprecated
    public void configure(Map<String, String> authParams) {
    }

    @Override
    public void start() {
    }

    @Override
    public void close() throws IOException {
    }
}
