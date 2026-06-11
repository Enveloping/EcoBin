package org.enveloping.ecobin.framework.onenet;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.Key;
import java.util.Base64;

/**
 * OneNet 北向消息 {@code data} 字段的 AES 解密工具。
 * <p>
 * 与官方 SDK demo（{@code AESBase64Utils}）口径一致：
 * <ul>
 *   <li>算法：{@code AES/ECB/PKCS5Padding}（{@code Cipher.getInstance("AES")} 的默认变换）；</li>
 *   <li>密钥：消费组 KEY 的 {@code substring(8, 24)}（16 字节 = AES-128）；</li>
 *   <li>密文为 Base64 字符串，先 Base64 解码再解密，得到第二层明文 JSON。</li>
 * </ul>
 */
public final class OneNetCipher {

    private static final String ALGO = "AES";

    private OneNetCipher() {
    }

    /** OneNet 约定：AES 密钥取消费组 KEY 的第 8~24 位（16 字节）。 */
    public static String deriveKey(String secretKey) {
        return secretKey.substring(8, 24);
    }

    /**
     * 解密 OneNet 北向报文的 {@code data} 字段。
     *
     * @param dataBase64 第一层报文里的 {@code data}（Base64 密文）
     * @param secretKey  消费组 KEY（完整值，内部自取 substring(8,24)）
     * @return 解密后的第二层明文 JSON
     */
    public static String decrypt(String dataBase64, String secretKey) {
        try {
            Key key = new SecretKeySpec(deriveKey(secretKey).getBytes(StandardCharsets.UTF_8), ALGO);
            Cipher cipher = Cipher.getInstance(ALGO);
            cipher.init(Cipher.DECRYPT_MODE, key);
            byte[] decoded = Base64.getDecoder().decode(dataBase64);
            byte[] plain = cipher.doFinal(decoded);
            return new String(plain, StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("OneNet 报文解密失败: " + e.getMessage(), e);
        }
    }

    /**
     * 加密（仅供单元测试构造密文用；线上只解密不加密）。
     */
    public static String encrypt(String plain, String secretKey) {
        try {
            Key key = new SecretKeySpec(deriveKey(secretKey).getBytes(StandardCharsets.UTF_8), ALGO);
            Cipher cipher = Cipher.getInstance(ALGO);
            cipher.init(Cipher.ENCRYPT_MODE, key);
            byte[] encrypted = cipher.doFinal(plain.getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(encrypted);
        } catch (Exception e) {
            throw new IllegalStateException("OneNet 报文加密失败: " + e.getMessage(), e);
        }
    }
}
