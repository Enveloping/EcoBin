package org.enveloping.ecobin.framework.cos;

/**
 * 一笔订单的 4 张照片在 COS 上的对象路径（key）。由后端开门时确定性生成。
 * <p>
 * 槽位：开门前·箱外 / 开门前·箱内 / 关门后·箱外 / 关门后·箱内。
 * key 形如 {@code {sn}/{doorIndex}/{token}/open_outside.jpg}（token = deliveryToken / cleanOrderId）。
 * 设备按槽位直传到对应 key，无需与后端约定文件名；后端据此 key + baseUrl 预存订单照片 URL。
 *
 * @see CosTokenClient#buildPhotoKeys(String, Integer, String)
 */
public record CosPhotoKeys(
        String openOutside,
        String openInside,
        String closeOutside,
        String closeInside
) {
}
