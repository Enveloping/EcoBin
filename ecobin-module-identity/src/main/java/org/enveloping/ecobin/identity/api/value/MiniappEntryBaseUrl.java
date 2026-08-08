package org.enveloping.ecobin.identity.api.value;

import java.net.URI;
import java.net.URISyntaxException;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * 普通链接二维码的入口地址规则。
 *
 * <p>平台保存的是不含设备码的 HTTPS 基础地址；设备模块只在生成二维码时追加唯一的
 * {@code deviceCode}。规则集中在这里，避免配置端和二维码生成端各自解释。</p>
 */
public final class MiniappEntryBaseUrl {

    private static final String DEVICE_CODE_PARAMETER = "deviceCode";

    private MiniappEntryBaseUrl() {
    }

    public static boolean isValid(String value) {
        if (value == null || value.isBlank() || !value.equals(value.trim())) {
            return false;
        }
        try {
            URI uri = new URI(value);
            if (!"https".equalsIgnoreCase(uri.getScheme())
                    || uri.isOpaque()
                    || uri.getHost() == null
                    || uri.getHost().isBlank()
                    || uri.getRawUserInfo() != null
                    || uri.getRawFragment() != null) {
                return false;
            }
            return !hasDeviceCode(uri.getRawQuery());
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return false;
        }
    }

    public static String appendDeviceCode(
            String entryBaseUrl,
            String deviceCode) {
        if (!isValid(entryBaseUrl)) {
            throw new IllegalArgumentException(
                    "invalid miniapp entry base URL");
        }
        if (deviceCode == null || deviceCode.isBlank()) {
            throw new IllegalArgumentException("deviceCode is required");
        }
        URI uri = URI.create(entryBaseUrl);
        String separator = uri.getRawQuery() == null
                ? "?"
                : uri.getRawQuery().isEmpty() ? "" : "&";
        return entryBaseUrl + separator + DEVICE_CODE_PARAMETER + "="
                + URLEncoder.encode(deviceCode, StandardCharsets.UTF_8);
    }

    private static boolean hasDeviceCode(String rawQuery) {
        if (rawQuery == null || rawQuery.isEmpty()) {
            return false;
        }
        for (String pair : rawQuery.split("&", -1)) {
            int separator = pair.indexOf('=');
            String rawName = separator < 0 ? pair : pair.substring(0, separator);
            String name = URLDecoder.decode(
                    rawName, StandardCharsets.UTF_8);
            if (DEVICE_CODE_PARAMETER.equals(name)) {
                return true;
            }
        }
        return false;
    }
}
