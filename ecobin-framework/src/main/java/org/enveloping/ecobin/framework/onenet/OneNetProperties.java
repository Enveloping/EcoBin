package org.enveloping.ecobin.framework.onenet;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 中国移动 OneNet 物联网平台配置（设备命令<strong>下行</strong>：物模型服务调用 API）。
 * <p>
 * 下行所需的平台 API 凭证（{@code productId} + {@code accessKey}）需设备端配合、暂未到位：
 * 缺失时 {@link OneNetClient} 仅记录占位日志、不发起真实请求，不阻塞业务主流程。
 * <p>
 * 鉴权与端点细节（token 版本、{@code res} 资源串、调用路径）以 OneNet 官方「平台 API 安全鉴权 / 设备服务调用」
 * 文档为准，<strong>联调时校验</strong>；此处给出标准 OneNET token 算法的默认实现。
 *
 * @see OneNetSubscriptionProperties 上行（北向 MQ 消费）配置，二者互不依赖
 */
@Data
@Component
@ConfigurationProperties(prefix = "onenet")
public class OneNetProperties {

    /** 平台 API 基地址（OneNET Studio 物联网开放平台） */
    private String baseUrl = "https://iot-api.heclouds.com";

    /** 物模型服务调用路径（AIoT 融合平台「设备服务调用」API，见 docs/references/设备服务调用.md） */
    private String invokeServicePath = "/thingmodel/call-service";

    /** 产品ID */
    private String productId;

    /** 访问密钥（Base64 编码的 access key，用于生成鉴权 token） */
    private String accessKey;

    /** token 版本号（联调待校验） */
    private String version = "2022-05-01";

    /** token 有效期（秒），生成时叠加当前时间作为 et */
    private long tokenTtlSeconds = 3600;

    /** 配置是否齐全（齐全才会发起真实下发） */
    public boolean isConfigured() {
        return baseUrl != null && !baseUrl.isBlank()
                && productId != null && !productId.isBlank()
                && accessKey != null && !accessKey.isBlank();
    }
}
