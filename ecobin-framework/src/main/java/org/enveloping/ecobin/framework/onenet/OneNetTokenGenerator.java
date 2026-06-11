package org.enveloping.ecobin.framework.onenet;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * OneNET 平台 API 鉴权 token 生成（标准算法，联调待校验）。
 * <p>
 * token 形如 {@code version=..&res=..&et=..&method=..&sign=..}，其中
 * {@code sign = Base64(HmacSHA256(base64Decode(accessKey), et+"\n"+method+"\n"+res+"\n"+version))}，
 * {@code res} 与 {@code sign} 在拼接前做 URL 编码。生成的 token 放入 HTTP 头 {@code Authorization}。
 */
public final class OneNetTokenGenerator {

    private static final String METHOD = "sha256";

    private OneNetTokenGenerator() {
    }

    /**
     * @param version   token 版本号
     * @param res       资源串，如 {@code products/{productId}}
     * @param accessKey Base64 编码的 access key
     * @param ttlSeconds 有效期（秒）
     */
    public static String generate(String version, String res, String accessKey, long ttlSeconds) {
        try {
            String et = String.valueOf(System.currentTimeMillis() / 1000 + ttlSeconds);
            String forSign = et + "\n" + METHOD + "\n" + res + "\n" + version;

            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(Base64.getDecoder().decode(accessKey), "HmacSHA256"));
            byte[] signBytes = mac.doFinal(forSign.getBytes(StandardCharsets.UTF_8));
            String sign = Base64.getEncoder().encodeToString(signBytes);

            return "version=" + version
                    + "&res=" + URLEncoder.encode(res, StandardCharsets.UTF_8)
                    + "&et=" + et
                    + "&method=" + METHOD
                    + "&sign=" + URLEncoder.encode(sign, StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("OneNet token 生成失败: " + e.getMessage(), e);
        }
    }
}
