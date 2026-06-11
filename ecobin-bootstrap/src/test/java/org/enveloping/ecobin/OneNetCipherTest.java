package org.enveloping.ecobin;

import org.enveloping.ecobin.framework.onenet.OneNetCipher;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * OneNet 北向报文解密单测：验证与官方 SDK demo 同款 AES 口径（key=secretKey.substring(8,24)）的加解密往返。
 * 无需凭证/broker。
 */
class OneNetCipherTest {

    /** 与 .env 同款 32 位消费组 KEY，substring(8,24) 取 16 字节作 AES-128 密钥。 */
    private static final String SECRET_KEY = "1fc9d2c3ddbd4fdca19a14b0dd788ff6";

    @Test
    void encryptThenDecrypt_roundTrips() {
        String plain = "{\"msgType\":\"thingEvent\",\"subData\":{\"deviceName\":\"EcoBin-SN-0001\"}}";

        String cipher = OneNetCipher.encrypt(plain, SECRET_KEY);
        String decrypted = OneNetCipher.decrypt(cipher, SECRET_KEY);

        assertThat(cipher).isNotEqualTo(plain);          // 确已加密
        assertThat(decrypted).isEqualTo(plain);          // 解密还原
    }

    @Test
    void deriveKey_takesSubstring8To24() {
        assertThat(OneNetCipher.deriveKey(SECRET_KEY)).isEqualTo("ddbd4fdca19a14b0").hasSize(16);
    }
}
