package org.enveloping.ecobin.integration.onenet.inbound;

import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.apache.pulsar.client.api.Consumer;
import org.apache.pulsar.client.api.Message;
import org.apache.pulsar.client.api.PulsarClient;
import org.apache.pulsar.client.api.Schema;
import org.apache.pulsar.client.api.SubscriptionType;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.SmartLifecycle;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.slf4j.MDC;
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
 *   <li>交 {@link OneNetMessageHandler} 分发；local-real 可记录脱敏、限长后的明文诊断载荷，绝不记录密文或凭证；</li>
 *   <li>仅处理成功或永久无效报文 ACK，暂时性处理失败使用 negative ACK 重投。</li>
 * </ol>
 * 仅当凭证齐全、{@code enabled=true} 且非 {@code test} 环境时启动；否则记日志跳过，不影响应用启动。
 */
@Slf4j
@Component
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
public class OneNetMqConsumer implements SmartLifecycle {

    private final OneNetSubscriptionProperties properties;
    private final ObjectProvider<OneNetMessageHandler> handlerProvider;
    private final ObjectMapper objectMapper;
    private final Environment environment;
    private final OneNetDiagnosticLogger diagnosticLogger;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private volatile PulsarClient client;
    private volatile Consumer<byte[]> consumer;
    private volatile Thread worker;

    public OneNetMqConsumer(OneNetSubscriptionProperties properties,
                            ObjectProvider<OneNetMessageHandler> handlerProvider,
                            ObjectMapper objectMapper,
                            Environment environment,
                            OneNetDiagnosticLogger diagnosticLogger) {
        this.properties = properties;
        this.handlerProvider = handlerProvider;
        this.objectMapper = objectMapper;
        this.environment = environment;
        this.diagnosticLogger = diagnosticLogger;
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
        log.info("[OneNet·MQ] 北向消费者已启动 broker={} subscription={}",
                properties.getBrokerUrl(), properties.getSubscriptionName());
    }

    private void runLoop() {
        long connectionStartedAt = diagnosticLogger.started();
        try {
            client = PulsarClient.builder()
                    .serviceUrl(properties.getBrokerUrl())
                    .allowTlsInsecureConnection(false)
                    .enableTlsHostnameVerification(true)
                    .authentication(new OneNetAuthentication(properties.getAccessId(), properties.getSecretKey()))
                    .build();
            consumer = client.newConsumer(Schema.BYTES)
                    .topic(String.format("%s/iot/event", properties.getAccessId()))
                    .subscriptionName(properties.getSubscriptionName())
                    .subscriptionType(SubscriptionType.Failover)
                    .autoUpdatePartitions(Boolean.FALSE)
                    .subscribe();
        } catch (Exception e) {
            log.error(
                    "[OneNet·MQ] 消费者连接失败，北向上行暂不可用（不影响其它业务）",
                    diagnosticLogger.sanitized(e));
            diagnosticLogger.inboundFailure(
                    null,
                    "MQ_CONNECTION",
                    "CONNECTION_FAILURE",
                    false,
                    0,
                    null,
                    e,
                    connectionStartedAt);
            running.set(false);
            return;
        }

        while (running.get() && !Thread.currentThread().isInterrupted()) {
            Message<byte[]> message = null;
            boolean acknowledge = false;
            try {
                message = consumer.receive();
                String messageId = message.getMessageId().toString();
                try (MDC.MDCCloseable ignored = MDC.putCloseable(
                        "mqMessageId", messageId)) {
                    acknowledge = dispatch(
                            new String(
                                    message.getData(),
                                    java.nio.charset.StandardCharsets.UTF_8),
                            messageId);
                }
            } catch (Exception e) {
                if (running.get()) {
                    log.error(
                            "[OneNet·MQ] 处理消息异常 type={}",
                            e.getClass().getSimpleName(),
                            diagnosticLogger.sanitized(e));
                }
            } finally {
                if (message != null) {
                    try {
                        // acknowledge=true 只在消息已可靠落库或已判定为永久毒消息时出现。
                        // 暂时性失败 negative ACK，让 Pulsar 保留并重投原消息。
                        if (acknowledge) {
                            consumer.acknowledge(message);
                        } else {
                            consumer.negativeAcknowledge(message);
                        }
                    } catch (Exception ackEx) {
                        log.warn(
                                "[OneNet·MQ] 消息确认操作失败 type={}",
                                ackEx.getClass().getSimpleName(),
                                diagnosticLogger.sanitized(ackEx));
                        diagnosticLogger.inboundFailure(
                                message.getMessageId().toString(),
                                "TRANSPORT_ACK",
                                "ACK_OPERATION_FAILURE",
                                false,
                                message.getData().length,
                                null,
                                ackEx,
                                diagnosticLogger.started());
                    }
                }
            }
        }
    }

    /**
     * 解第一层、解密并交分发器。永久无效传输 ACK 以隔离毒消息；
     * 缺少处理器或业务处理异常返回 false 触发重投。
     */
    private boolean dispatch(String envelope, String mqMessageId) {
        long startedAt = diagnosticLogger.started();
        int transportBytes = envelope.getBytes(
                java.nio.charset.StandardCharsets.UTF_8).length;
        String decrypted;
        try {
            JsonNode root = objectMapper.readTree(envelope);
            String data = root.path("data").asString();
            if (data == null || data.isBlank()) {
                log.warn(
                        "[OneNet·MQ] 永久无效报文缺少 data messageId={}",
                        mqMessageId);
                diagnosticLogger.inboundFailure(
                        mqMessageId,
                        "TRANSPORT_ENVELOPE",
                        "MISSING_ENCRYPTED_DATA",
                        true,
                        transportBytes,
                        null,
                        null,
                        startedAt);
                return true;
            }
            decrypted = OneNetCipher.decrypt(data, properties.getSecretKey());
        } catch (Exception e) {
            log.error(
                    "[OneNet·MQ] 永久无效报文解包或认证失败 messageId={} type={}",
                    mqMessageId,
                    e.getClass().getSimpleName(),
                    diagnosticLogger.sanitized(e));
            diagnosticLogger.inboundFailure(
                    mqMessageId,
                    "TRANSPORT_DECRYPTION",
                    "PERMANENT_TRANSPORT_REJECTION",
                    true,
                    transportBytes,
                    null,
                    e,
                    startedAt);
            return true;
        }

        diagnosticLogger.inboundMessage(
                mqMessageId, transportBytes, decrypted);

        OneNetMessageHandler handler = handlerProvider.getIfAvailable();
        if (handler == null) {
            log.error(
                    "[OneNet·MQ] 无消息处理器，暂不确认 messageId={}",
                    mqMessageId);
            diagnosticLogger.inboundFailure(
                    mqMessageId,
                    "HANDLER_RESOLUTION",
                    "HANDLER_UNAVAILABLE",
                    false,
                    transportBytes,
                    decrypted,
                    null,
                    startedAt);
            return false;
        }
        try {
            handler.handle(
                    decrypted,
                    mqMessageId,
                    envelope.getBytes(
                            java.nio.charset.StandardCharsets.UTF_8));
            diagnosticLogger.inboundOutcome(
                    mqMessageId,
                    "DURABLY_ACCEPTED",
                    true,
                    startedAt);
            return true;
        } catch (OneNetPermanentMessageException e) {
            log.warn(
                    "[OneNet·MQ] 永久无效业务报文已安全拒绝 messageId={} type={}",
                    mqMessageId,
                    e.getClass().getSimpleName(),
                    diagnosticLogger.sanitized(e));
            diagnosticLogger.inboundFailure(
                    mqMessageId,
                    "BUSINESS_DISPATCH",
                    "PERMANENT_MESSAGE_REJECTION",
                    true,
                    transportBytes,
                    decrypted,
                    e,
                    startedAt);
            return true;
        } catch (Exception e) {
            log.error(
                    "[OneNet·MQ] 分发处理失败，消息将重投 messageId={} type={}",
                    mqMessageId,
                    e.getClass().getSimpleName(),
                    diagnosticLogger.sanitized(e));
            diagnosticLogger.inboundFailure(
                    mqMessageId,
                    "BUSINESS_DISPATCH",
                    "RETRYABLE_DISPATCH_FAILURE",
                    false,
                    transportBytes,
                    decrypted,
                    e,
                    startedAt);
            return false;
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
            log.warn(
                    "[OneNet·MQ] 关闭 consumer 异常",
                    diagnosticLogger.sanitized(e));
        }
        try {
            if (client != null) {
                client.close();
            }
        } catch (Exception e) {
            log.warn(
                    "[OneNet·MQ] 关闭 client 异常",
                    diagnosticLogger.sanitized(e));
        }
        log.info("[OneNet·MQ] 北向消费者已停止");
    }

    @Override
    public boolean isRunning() {
        return running.get();
    }
}
