package com.chinamobile.iot.pulsar.auth;

import org.apache.pulsar.client.api.AuthenticationDataProvider;
import org.apache.pulsar.shade.org.apache.commons.codec.digest.DigestUtils;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;


public class IoTAuthenticationDataProvider implements AuthenticationDataProvider {
    /**
     * 鉴权的token
     */
    private String token;

    private Map<String, String> headers = new HashMap<>();
    private String header = "iot_auth";

    private static final String methodName = "iot-auth";
    //默认方法
    public IoTAuthenticationDataProvider(){
    }
    public IoTAuthenticationDataProvider(String iotAccessId, String iotSecretKey){
        this.token = String.format("{\"tenant\":\"%s\",\"password\":\"%s\"}", iotAccessId,
                DigestUtils.sha256Hex(iotAccessId + DigestUtils.sha256Hex(iotSecretKey)).substring(4, 20));
    }




    @Override
    public boolean hasDataForHttp() {
        return false;
    }

    @Override
    public Set<Map.Entry<String, String>> getHttpHeaders() throws Exception {
        return null;
    }

    @Override
    public boolean hasDataFromCommand() {
        return true;
    }

    @Override
    public String getCommandData() {
        return token;
    }
}

