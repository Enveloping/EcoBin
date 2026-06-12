package com.chinamobile.iot.pulsar.auth;


import org.apache.pulsar.client.api.Authentication;
import org.apache.pulsar.client.api.AuthenticationDataProvider;
import org.apache.pulsar.client.api.EncodedAuthenticationParameterSupport;
import org.apache.pulsar.client.api.PulsarClientException;

import java.io.IOException;
import java.util.Map;

public class IoTAuthentication implements Authentication, EncodedAuthenticationParameterSupport {
    private static final String methodName = "iot-auth";
    private String iotAccessId;
    private String iotSecretKey;


    //提供一个默认构造方法
    public IoTAuthentication() {
    }


    public IoTAuthentication(String iotAccessId, String iotSecretKey) {
        this.iotAccessId = iotAccessId;
        this.iotSecretKey = iotSecretKey;
    }

    /**
     * 获取鉴权方法
     *
     * @return
     */
    @Override
    public String getAuthMethodName() {
        return methodName;
    }

    /**
     * 获取鉴权数据
     *
     * @return 自定义鉴权数据对象
     * @throws PulsarClientException
     */
    @Override
    public AuthenticationDataProvider getAuthData() throws PulsarClientException {
        return new IoTAuthenticationDataProvider(this.iotAccessId, this.iotSecretKey);
    }


    @Override
    public void configure(String encodedAuthParamString) {
    }


    @Deprecated
    @Override
    public void configure(Map<String, String> authParams) {
    }

    /**
     * start
     *
     * @throws PulsarClientException
     */
    @Override
    public void start() throws PulsarClientException {
    }

    @Override
    public void close() throws IOException {
    }

}
