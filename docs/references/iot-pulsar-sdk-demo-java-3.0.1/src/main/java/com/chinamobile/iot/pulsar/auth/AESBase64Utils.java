package com.chinamobile.iot.pulsar.auth;

import org.apache.pulsar.shade.org.apache.commons.codec.binary.Base64;
import org.apache.pulsar.shade.org.apache.commons.codec.binary.StringUtils;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;
import java.security.Key;

public class AESBase64Utils {

    // 加密算法
    private final String ALGO="AES";

    private byte[] keyValue;


    /**
     * 解密
     */
    public String decrypt(String encryptedData) throws Exception {
        Key key = generateKey();
        Cipher c = Cipher.getInstance(ALGO);
        c.init(Cipher.DECRYPT_MODE, key);
        byte[] decodedValue = Base64.decodeBase64(encryptedData);
        byte[] decValue = c.doFinal(decodedValue);
        String decryptedValue = StringUtils.newStringUtf8(decValue);
        return decryptedValue;
    }

    /**
     * 生成Key
     */
    private Key generateKey() {
        Key key = new SecretKeySpec(keyValue, ALGO);
        return key;
    }

    public void setKeyValue(byte[] keyValue) {
        this.keyValue = keyValue;
    }

    public static String decrypt(String data, String secretKey) throws Exception {
        // 创建加解密类
        AESBase64Utils aes = new AESBase64Utils();
        // 设置加解密密钥
        aes.setKeyValue(secretKey.getBytes());
        // 解密后的字符串
        return aes.decrypt(data);
    }

}
