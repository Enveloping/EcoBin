package org.enveloping.ecobin.framework.crypto;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

/**
 * AES 对称加解密工具，用于存储 {@code sys_tenant.miniapp_secret} 等敏感字段。
 * <p>
 * 密钥来自配置 {@code app.crypto.aes-key}，经 SHA-256 派生为 128bit AES 密钥，
 * 因此对配置密钥长度无限制。采用 AES/ECB/PKCS5Padding，密文 Base64 编码。
 */
@Component
public class AesCryptoUtil {

    private static final String TRANSFORMATION = "AES/ECB/PKCS5Padding";

    private final SecretKeySpec keySpec;

    public AesCryptoUtil(@Value("${app.crypto.aes-key:EcoBin_Default_AES_Key_Change_In_Production}") String aesKey) {
        this.keySpec = deriveKey(aesKey);
    }

    private static SecretKeySpec deriveKey(String aesKey) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(aesKey.getBytes(StandardCharsets.UTF_8));
            byte[] key128 = new byte[16];
            System.arraycopy(digest, 0, key128, 0, 16);
            return new SecretKeySpec(key128, "AES");
        } catch (Exception e) {
            throw new IllegalStateException("初始化 AES 密钥失败", e);
        }
    }

    /** 加密明文，返回 Base64 密文；入参为 null 时返回 null。 */
    public String encrypt(String plainText) {
        if (plainText == null) {
            return null;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, keySpec);
            byte[] encrypted = cipher.doFinal(plainText.getBytes(StandardCharsets.UTF_8));
            return Base64.getEncoder().encodeToString(encrypted);
        } catch (Exception e) {
            throw new IllegalStateException("AES 加密失败", e);
        }
    }

    /** 解密 Base64 密文，返回明文；入参为 null 时返回 null。 */
    public String decrypt(String cipherText) {
        if (cipherText == null) {
            return null;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, keySpec);
            byte[] decoded = Base64.getDecoder().decode(cipherText);
            return new String(cipher.doFinal(decoded), StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("AES 解密失败", e);
        }
    }
}
