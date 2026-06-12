package org.enveloping.ecobin.framework.onenet;

import lombok.extern.slf4j.Slf4j;
import org.apache.pulsar.client.api.Consumer;
import org.apache.pulsar.client.api.Message;
import org.apache.pulsar.client.api.PulsarClient;
import org.apache.pulsar.client.api.Schema;
import org.apache.pulsar.client.api.SubscriptionType;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.context.SmartLifecycle;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * OneNet 北向消息队列消费者（设备 → 后端上行）。
 * <p>
 * 作为 Pulsar 消费者主动连 OneNet MQ（免公网），订阅 {@code <accessId>/iot/event}：
 * <ol>
 *   <li>解析第一层报文 {@code {superMsg,pv,t,data,sign}}，取出 {@code data}（Base64 密文）；</li>
 *   <li>用消费组 KEY 经 {@link OneNetCipher} 解密得到第二层明文 JSON；</li>
 *   <li>INFO 打印整条明文（联调据此核对 {@code params} 形状），交 {@link OneNetMessageHandler} 分发；</li>
 *   <li>无论成功失败都 {@code acknowledge}（at-least-once，幂等由业务保证），异常不中断循环。</li>
 * </ol>
 * 仅当凭证齐全、{@code enabled=true} 且非 {@code test} 环境时启动；否则记日志跳过，不影响应用启动。
 */
@Slf4j
@Component
public class OneNetMqConsumer implements SmartLifecycle {

    private final OneNetSubscriptionProperties properties;
    private final ObjectProvider<OneNetMessageHandler> handlerProvider;
    private final ObjectMapper objectMapper;
    private final Environment environment;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private volatile PulsarClient client;
    private volatile Consumer<byte[]> consumer;
    private volatile Thread worker;

    public OneNetMqConsumer(OneNetSubscriptionProperties properties,
                            ObjectProvider<OneNetMessageHandler> handlerProvider,
                            ObjectMapper objectMapper,
                            Environment environment) {
        this.properties = properties;
        this.handlerProvider = handlerProvider;
        this.objectMapper = objectMapper;
        this.environment = environment;
    }

    @Override
    public void start() {
        if (!properties.isEnabled()) {
            log.info("[OneNet·MQ] onenet.subscription.enabled=false，跳过北向消费者启动");
            return;
        }
        if (environment.matchesProfiles("test")) {
            log.info("[OneNet·MQ] test 环境，跳过北向消费者启动");
            return;
        }
        if (!properties.isConfigured()) {
            log.info("[OneNet·MQ] 消费组凭证未配置（accessId/secretKey/subscriptionName），跳过北向消费者启动");
            return;
        }
        if (!running.compareAndSet(false, true)) {
            return;
        }
        worker = new Thread(this::runLoop, "onenet-mq-consumer");
        worker.setDaemon(true);
        worker.start();
        log.info("[OneNet·MQ] 北向消费者已启动 broker={}, accessId={}, subscription={}",
                properties.getBrokerUrl(), properties.getAccessId(), properties.getSubscriptionName());
    }

    private void runLoop() {
        try {
            client = PulsarClient.builder()
                    .serviceUrl(properties.getBrokerUrl())
                    .allowTlsInsecureConnection(true)
                    .authentication(new OneNetAuthentication(properties.getAccessId(), properties.getSecretKey()))
                    .build();
            consumer = client.newConsumer(Schema.BYTES)
                    .topic(String.format("%s/iot/event", properties.getAccessId()))
                    .subscriptionName(properties.getSubscriptionName())
                    .subscriptionType(SubscriptionType.Failover)
                    .autoUpdatePartitions(Boolean.FALSE)
                    .subscribe();
        } catch (Exception e) {
            log.error("[OneNet·MQ] 消费者连接失败，北向上行暂不可用（不影响其它业务）", e);
            running.set(false);
            return;
        }

        while (running.get() && !Thread.currentThread().isInterrupted()) {
            Message<byte[]> message = null;
            try {
                message = consumer.receive();
                dispatch(new String(message.getData(), java.nio.charset.StandardCharsets.UTF_8),
                        message.getMessageId().toString());
            } catch (Exception e) {
                if (running.get()) {
                    log.error("[OneNet·MQ] 处理消息异常", e);
                }
            } finally {
                if (message != null) {
                    try {
                        consumer.acknowledge(message);
                    } catch (Exception ackEx) {
                        log.warn("[OneNet·MQ] ack 失败", ackEx);
                    }
                }
            }
        }
    }

    /** 解第一层 → 解密 → 打印 → 交分发器（透传 MQ 消息 id 作幂等兜底）。 */
    private void dispatch(String envelope, String mqMessageId) {
        String decrypted;
        try {
            JsonNode root = objectMapper.readTree(envelope);
            String data = root.path("data").asString();
            if (data == null || data.isBlank()) {
                log.warn("[OneNet·MQ] 报文缺少 data 字段，原文={}", envelope);
                return;
            }
            decrypted = OneNetCipher.decrypt(data, properties.getSecretKey());
        } catch (Exception e) {
            log.error("[OneNet·MQ] 解包/解密失败，原文={}", envelope, e);
            return;
        }

        log.info("[OneNet·MQ] 收到上行明文：{}", decrypted);
        OneNetMessageHandler handler = handlerProvider.getIfAvailable();
        if (handler == null) {
            log.warn("[OneNet·MQ] 无 OneNetMessageHandler 实现，消息仅打印不分发");
            return;
        }
        try {
            handler.handle(decrypted, mqMessageId);
        } catch (Exception e) {
            log.error("[OneNet·MQ] 分发处理异常（已忽略，消息将被 ack）", e);
        }
    }

    @Override
    public void stop() {
        running.set(false);
        if (worker != null) {
            worker.interrupt();
        }
        try {
            if (consumer != null) {
                consumer.close();
            }
        } catch (Exception e) {
            log.warn("[OneNet·MQ] 关闭 consumer 异常", e);
        }
        try {
            if (client != null) {
                client.close();
            }
        } catch (Exception e) {
            log.warn("[OneNet·MQ] 关闭 client 异常", e);
        }
        log.info("[OneNet·MQ] 北向消费者已停止");
    }

    @Override
    public boolean isRunning() {
        return running.get();
    }
}
