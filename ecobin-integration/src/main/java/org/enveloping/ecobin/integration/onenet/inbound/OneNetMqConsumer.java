package org.enveloping.ecobin.integration.onenet.inbound;

import lombok.extern.slf4j.Slf4j;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.apache.pulsar.client.api.Consumer;
import org.apache.pulsar.client.api.Message;
import org.apache.pulsar.client.api.PulsarClient;
import org.apache.pulsar.client.api.Schema;
import org.apache.pulsar.client.api.SubscriptionType;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.SmartLifecycle;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.slf4j.MDC;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.Objects;
import java.util.concurrent.TimeUnit;
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

    private static final long INITIAL_RETRY_DELAY_MILLIS = 1_000L;
    private static final long MAXIMUM_RETRY_DELAY_MILLIS = 30_000L;
    private static final int SUBSCRIPTION_CONNECT_TIMEOUT_SECONDS = 10;

    @FunctionalInterface
    interface SubscriptionConnector {
        SubscriptionConnection connect(
                OneNetSubscriptionProperties properties,
                ConnectingClientRegistrar registrar) throws Exception;
    }

    @FunctionalInterface
    interface ConnectingClientRegistrar {
        boolean register(PulsarClient client);
    }

    record SubscriptionConnection(
            PulsarClient client,
            Consumer<byte[]> consumer) {

        SubscriptionConnection {
            Objects.requireNonNull(client, "client");
            Objects.requireNonNull(consumer, "consumer");
        }
    }

    private final OneNetSubscriptionProperties properties;
    private final ObjectProvider<OneNetMessageHandler> handlerProvider;
    private final ObjectMapper objectMapper;
    private final Environment environment;
    private final OneNetDiagnosticLogger diagnosticLogger;
    private final SubscriptionConnector subscriptionConnector;
    private final long initialRetryDelayMillis;
    private final long maximumRetryDelayMillis;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicBoolean stopped = new AtomicBoolean(false);
    private final Object connectionLock = new Object();
    private volatile PulsarClient connectingClient;
    private volatile PulsarClient closedConnectingClient;
    private volatile PulsarClient client;
    private volatile Consumer<byte[]> consumer;
    private volatile Thread worker;

    @Autowired
    public OneNetMqConsumer(OneNetSubscriptionProperties properties,
                            ObjectProvider<OneNetMessageHandler> handlerProvider,
                            ObjectMapper objectMapper,
                            Environment environment,
                            OneNetDiagnosticLogger diagnosticLogger) {
        this(
                properties,
                handlerProvider,
                objectMapper,
                environment,
                diagnosticLogger,
                OneNetMqConsumer::openSubscription,
                INITIAL_RETRY_DELAY_MILLIS,
                MAXIMUM_RETRY_DELAY_MILLIS);
    }

    OneNetMqConsumer(OneNetSubscriptionProperties properties,
                     ObjectProvider<OneNetMessageHandler> handlerProvider,
                     ObjectMapper objectMapper,
                     Environment environment,
                     OneNetDiagnosticLogger diagnosticLogger,
                     SubscriptionConnector subscriptionConnector,
                     long initialRetryDelayMillis,
                     long maximumRetryDelayMillis) {
        if (initialRetryDelayMillis <= 0
                || maximumRetryDelayMillis < initialRetryDelayMillis) {
            throw new IllegalArgumentException(
                    "invalid OneNet subscription retry delays");
        }
        this.properties = properties;
        this.handlerProvider = handlerProvider;
        this.objectMapper = objectMapper;
        this.environment = environment;
        this.diagnosticLogger = diagnosticLogger;
        this.subscriptionConnector = subscriptionConnector;
        this.initialRetryDelayMillis = initialRetryDelayMillis;
        this.maximumRetryDelayMillis = maximumRetryDelayMillis;
    }

    @Override
    public void start() {
        if (stopped.get()) {
            return;
        }
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
        // stop() may win after the first lifecycle check but before the CAS.
        // Recheck the permanent stop fence before creating a worker so a
        // destroyed bean cannot make even one later connection attempt.
        if (stopped.get()) {
            running.set(false);
            return;
        }
        worker = new Thread(this::runLoop, "onenet-mq-consumer");
        worker.setDaemon(true);
        worker.start();
        log.info("[OneNet·MQ] 北向消费者已启动 broker={} subscription={}",
                properties.getBrokerUrl(), properties.getSubscriptionName());
    }

    private void runLoop() {
        try {
            long retryDelayMillis = initialRetryDelayMillis;
            while (running.get()
                    && !Thread.currentThread().isInterrupted()) {
                long connectionStartedAt = diagnosticLogger.started();
                long connectionInstalledAtNanos = 0L;
                AtomicBoolean receivedMessage = new AtomicBoolean(false);
                try {
                    SubscriptionConnection connection =
                            subscriptionConnector.connect(
                                    properties,
                                    this::installConnectingClient);
                    if (!installConnection(connection)) {
                        closeConnection(connection);
                        return;
                    }
                    connectionInstalledAtNanos = System.nanoTime();
                    consume(
                            connection.consumer(),
                            () -> receivedMessage.set(true));
                    return;
                } catch (Exception failure) {
                    // A successful subscription can still be invalidated by a
                    // broker/network failure in receive().  Remove and close
                    // that exact connection before attempting a replacement;
                    // otherwise installConnection would reject the retry and
                    // the old consumer could spin or leak indefinitely.
                    closeInstalledConnection();
                    if (!running.get()
                            || Thread.currentThread().isInterrupted()) {
                        return;
                    }
                    if (receivedMessage.get()
                            || connectionInstalledAtNanos != 0L
                            && System.nanoTime() - connectionInstalledAtNanos
                            >= maximumRetryDelayMillis * 1_000_000L) {
                        retryDelayMillis = initialRetryDelayMillis;
                    }
                    log.error(
                            "[OneNet·MQ] 消费者连接失败，将在 {}ms 后重试",
                            retryDelayMillis,
                            diagnosticLogger.sanitized(failure));
                    diagnosticLogger.inboundFailure(
                            null,
                            "MQ_CONNECTION",
                            "CONNECTION_FAILURE_RETRYING",
                            false,
                            0,
                            null,
                            failure,
                            connectionStartedAt);
                    if (!waitForRetry(retryDelayMillis)) {
                        return;
                    }
                    retryDelayMillis = nextRetryDelay(
                            retryDelayMillis,
                            maximumRetryDelayMillis);
                }
            }
        } finally {
            closeInstalledConnection();
            synchronized (connectionLock) {
                closedConnectingClient = null;
            }
            running.set(false);
        }
    }

    private void consume(
            Consumer<byte[]> activeConsumer,
            Runnable onMessageReceived) throws Exception {
        while (running.get() && !Thread.currentThread().isInterrupted()) {
            // A receive failure is a connection failure, not a malformed
            // message. Let the outer loop close this subscription and apply
            // bounded retry backoff instead of immediately calling receive()
            // again on the same broken consumer.
            Message<byte[]> message = activeConsumer.receive();
            Objects.requireNonNull(message, "OneNet receive returned null");
            onMessageReceived.run();
            boolean acknowledge = false;
            try {
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
                try {
                    // acknowledge=true 只在消息已可靠落库或已判定为永久毒消息时出现。
                    // 暂时性失败 negative ACK，让 Pulsar 保留并重投原消息。
                    if (acknowledge) {
                        activeConsumer.acknowledge(message);
                    } else {
                        activeConsumer.negativeAcknowledge(message);
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

    private boolean installConnection(SubscriptionConnection connection) {
        synchronized (connectionLock) {
            if (!running.get() || stopped.get()) {
                connectingClient = null;
                return false;
            }
            if (client != null || consumer != null) {
                throw new IllegalStateException(
                        "OneNet subscription connection is already installed");
            }
            if (connectingClient != null
                    && connectingClient != connection.client()) {
                throw new IllegalStateException(
                        "OneNet connecting client differs from subscription");
            }
            connectingClient = null;
            client = connection.client();
            consumer = connection.consumer();
            return true;
        }
    }

    private boolean installConnectingClient(PulsarClient candidate) {
        Objects.requireNonNull(candidate, "candidate");
        synchronized (connectionLock) {
            if (!running.get() || stopped.get()) {
                return false;
            }
            if (connectingClient != null || client != null || consumer != null) {
                throw new IllegalStateException(
                        "OneNet subscription connection is already present");
            }
            connectingClient = candidate;
            return true;
        }
    }

    private boolean waitForRetry(long retryDelayMillis) {
        try {
            Thread.sleep(retryDelayMillis);
            return running.get() && !stopped.get();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private static long nextRetryDelay(long current, long maximum) {
        return current >= maximum / 2
                ? maximum
                : Math.min(current * 2, maximum);
    }

    private static SubscriptionConnection openSubscription(
            OneNetSubscriptionProperties properties,
            ConnectingClientRegistrar registrar) throws Exception {
        PulsarClient client = PulsarClient.builder()
                .serviceUrl(properties.getBrokerUrl())
                .connectionTimeout(
                        SUBSCRIPTION_CONNECT_TIMEOUT_SECONDS,
                        TimeUnit.SECONDS)
                .lookupTimeout(
                        SUBSCRIPTION_CONNECT_TIMEOUT_SECONDS,
                        TimeUnit.SECONDS)
                .operationTimeout(
                        SUBSCRIPTION_CONNECT_TIMEOUT_SECONDS,
                        TimeUnit.SECONDS)
                .allowTlsInsecureConnection(false)
                .enableTlsHostnameVerification(true)
                .authentication(new OneNetAuthentication(
                        properties.getAccessId(), properties.getSecretKey()))
                .build();
        boolean registered = false;
        try {
            if (!registrar.register(client)) {
                throw new InterruptedException(
                        "OneNet subscription stopped before subscribe");
            }
            registered = true;
            Consumer<byte[]> consumer = client.newConsumer(Schema.BYTES)
                    .topic(String.format(
                            "%s/iot/event", properties.getAccessId()))
                    .subscriptionName(properties.getSubscriptionName())
                    .subscriptionType(SubscriptionType.Failover)
                    .autoUpdatePartitions(Boolean.FALSE)
                    .subscribe();
            return new SubscriptionConnection(client, consumer);
        } catch (Exception failure) {
            // Once registered, the lifecycle owner closes the in-progress
            // client (including stop while subscribe blocks).  Before
            // registration this method still owns it.
            if (!registered) {
                try {
                    client.close();
                } catch (Exception closeFailure) {
                    failure.addSuppressed(closeFailure);
                }
            }
            throw failure;
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
        // Bean-destruction fence: this instance is intentionally not restartable.
        stopped.set(true);
        running.set(false);
        Thread activeWorker = worker;
        if (activeWorker != null) {
            activeWorker.interrupt();
        }
        closeInstalledConnection();
        log.info("[OneNet·MQ] 北向消费者已停止");
    }

    private void closeInstalledConnection() {
        SubscriptionConnection connection;
        PulsarClient pendingClient;
        synchronized (connectionLock) {
            if (client == null && consumer == null && connectingClient == null) {
                return;
            }
            if ((client == null) != (consumer == null)) {
                throw new IllegalStateException(
                        "OneNet subscription connection is incomplete");
            }
            connection = client == null
                    ? null
                    : new SubscriptionConnection(client, consumer);
            pendingClient = connectingClient;
            if (pendingClient != null) {
                closedConnectingClient = pendingClient;
            }
            connectingClient = null;
            client = null;
            consumer = null;
        }
        if (connection != null) {
            closeConnection(connection);
        }
        if (pendingClient != null
                && (connection == null || pendingClient != connection.client())) {
            closeClient(pendingClient);
        }
    }

    private void closeConnection(SubscriptionConnection connection) {
        try {
            connection.consumer().close();
        } catch (Exception e) {
            log.warn(
                    "[OneNet·MQ] 关闭 consumer 异常",
                    diagnosticLogger.sanitized(e));
        }
        boolean clientAlreadyClosed;
        synchronized (connectionLock) {
            clientAlreadyClosed = closedConnectingClient == connection.client();
            if (clientAlreadyClosed) {
                closedConnectingClient = null;
            }
        }
        if (!clientAlreadyClosed) {
            closeClient(connection.client());
        }
    }

    private void closeClient(PulsarClient candidate) {
        try {
            candidate.close();
        } catch (Exception e) {
            log.warn(
                    "[OneNet·MQ] 关闭 client 异常",
                    diagnosticLogger.sanitized(e));
        }
    }

    @Override
    public boolean isRunning() {
        return running.get();
    }
}
