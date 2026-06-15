package org.enveloping.ecobin.framework.onenet;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 中国移动 OneNet 北向消息队列（服务端订阅）消费配置。
 * <p>
 * 对应设备 → 后端的<strong>上行</strong>链路：后端作 Pulsar 消费者主动连 OneNet MQ 实例（免公网）。
 * 凭证（消费组 ID/KEY、订阅名）由 {@code .env} 经 {@code spring.config.import} 注入；
 * 三者齐全且 {@link #enabled} 为真、且非测试环境时，{@link OneNetMqConsumer} 才启动。
 */
@Data
@Component
@ConfigurationProperties(prefix = "onenet.subscription")
public class OneNetSubscriptionProperties {

    /** Pulsar broker 地址（OneNet 北向 MQ，默认官方上海节点） */
    private String brokerUrl = "pulsar+ssl://iot-north-mq.heclouds.com:6651/";

    /** 消费组 ID（OneNet 控制台「服务端订阅」生成，亦即 topic 前缀） */
    private String accessId;

    /** 消费组 KEY（用于 Pulsar 自定义鉴权 + data 字段 AES 解密） */
    private String secretKey;

    /** 订阅名称（OneNet 控制台配置） */
    private String subscriptionName;

    /** 是否启用消费者（测试环境置 false） */
    private boolean enabled = true;

    /** 凭证是否齐全（齐全才会真正连 OneNet MQ） */
    public boolean isConfigured() {
        return accessId != null && !accessId.isBlank()
                && secretKey != null && !secretKey.isBlank()
                && subscriptionName != null && !subscriptionName.isBlank();
    }
}
