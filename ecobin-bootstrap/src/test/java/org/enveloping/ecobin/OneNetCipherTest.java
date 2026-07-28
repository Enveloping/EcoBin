package org.enveloping.ecobin;

import org.enveloping.ecobin.integration.onenet.inbound.OneNetCipher;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * OneNet 北向报文解密单测：验证与官方 SDK demo 同款 AES 口径
 * （key=secretKey.substring(8,24)）的加解密往返，无需凭证或 broker。
 */
class OneNetCipherTest {

    /** 合成的 32 位测试 KEY，派生出的中间 16 字节也是明确测试数据。 */
    private static final String SECRET_KEY =
            "01234567abcdefghijklmnopABCDEFGH";

    @Test
    void encryptThenDecrypt_roundTrips() {
        String plain =
                "{\"msgType\":\"thingEvent\",\"subData\":{"
                        + "\"deviceName\":\"EcoBin-SN-0001\"}}";

        String cipher = OneNetCipher.encrypt(plain, SECRET_KEY);
        String decrypted = OneNetCipher.decrypt(cipher, SECRET_KEY);

        assertThat(cipher).isNotEqualTo(plain);
        assertThat(decrypted).isEqualTo(plain);
    }

    @Test
    void deriveKey_takesSubstring8To24() {
        assertThat(OneNetCipher.deriveKey(SECRET_KEY))
                .isEqualTo("abcdefghijklmnop")
                .hasSize(16);
    }
}
