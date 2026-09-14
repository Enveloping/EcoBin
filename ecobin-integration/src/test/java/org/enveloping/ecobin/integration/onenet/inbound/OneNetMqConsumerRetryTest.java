package org.enveloping.ecobin.integration.onenet.inbound;

import org.apache.pulsar.client.api.Consumer;
import org.apache.pulsar.client.api.PulsarClient;
import org.apache.pulsar.client.api.PulsarClientException;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.core.env.Environment;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OneNetMqConsumerRetryTest {

    private OneNetMqConsumer subject;

    @AfterEach
    void stopConsumer() {
        if (subject != null) {
            subject.stop();
        }
    }

    @Test
    void initialSubscriptionFailureRetriesUntilConsumerIsUsable()
            throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        CountDownLatch receiving = new CountDownLatch(1);
        Consumer<byte[]> consumer = blockingConsumer(receiving);
        PulsarClient client = mock(PulsarClient.class);

        subject = consumer(properties(), (properties, registrar) -> {
            if (attempts.incrementAndGet() == 1) {
                throw new PulsarClientException(
                        "injected initial subscription failure");
            }
            return new OneNetMqConsumer.SubscriptionConnection(
                    client, consumer);
        }, 10L, 20L);

        subject.start();

        assertThat(receiving.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(attempts).hasValue(2);
        assertThat(subject.isRunning()).isTrue();
        subject.stop();
        verify(consumer).close();
        verify(client).close();
        subject.start();
        await().during(Duration.ofMillis(100))
                .atMost(Duration.ofSeconds(1))
                .untilAsserted(() -> assertThat(attempts).hasValue(2));
    }

    @Test
    void stopDuringRetryDelayPreventsAnyLaterConnectionAttempt()
            throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        CountDownLatch firstFailure = new CountDownLatch(1);
        subject = consumer(properties(), (properties, registrar) -> {
            attempts.incrementAndGet();
            firstFailure.countDown();
            throw new PulsarClientException(
                    "injected persistent subscription failure");
        }, 5_000L, 5_000L);

        subject.start();
        assertThat(firstFailure.await(2, TimeUnit.SECONDS)).isTrue();
        subject.stop();

        await().during(Duration.ofMillis(100))
                .atMost(Duration.ofSeconds(1))
                .untilAsserted(() -> {
                    assertThat(attempts).hasValue(1);
                    assertThat(subject.isRunning()).isFalse();
                });
    }

    @Test
    void connectionThatFinishesAfterStopIsClosedWithoutBeingConsumed()
            throws Exception {
        CountDownLatch connecting = new CountDownLatch(1);
        CountDownLatch releaseConnection = new CountDownLatch(1);
        PulsarClient client = mock(PulsarClient.class);
        @SuppressWarnings("unchecked")
        Consumer<byte[]> consumer = mock(Consumer.class);
        subject = consumer(properties(), (properties, registrar) -> {
            assertThat(registrar.register(client)).isTrue();
            connecting.countDown();
            boolean interrupted = false;
            while (true) {
                try {
                    releaseConnection.await();
                    break;
                } catch (InterruptedException stopSignal) {
                    interrupted = true;
                }
            }
            if (interrupted) {
                Thread.currentThread().interrupt();
            }
            return new OneNetMqConsumer.SubscriptionConnection(
                    client, consumer);
        }, 10L, 20L);

        subject.start();
        assertThat(connecting.await(2, TimeUnit.SECONDS)).isTrue();
        subject.stop();
        // The client exists before subscribe returns, so stop must close it
        // without waiting for a connector that ignores thread interruption.
        verify(client).close();
        releaseConnection.countDown();

        await().atMost(Duration.ofSeconds(2)).untilAsserted(() -> {
            verify(consumer).close();
            verify(client, times(1)).close();
        });
        assertThat(subject.isRunning()).isFalse();
    }

    @Test
    void registeredSubscriptionFailureClosesClientOnceBeforeRetry()
            throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        PulsarClient failedClient = mock(PulsarClient.class);
        CountDownLatch replacementReceiving = new CountDownLatch(1);
        Consumer<byte[]> replacementConsumer =
                blockingConsumer(replacementReceiving);
        PulsarClient replacementClient = mock(PulsarClient.class);
        subject = consumer(properties(), (properties, registrar) -> {
            if (attempts.incrementAndGet() == 1) {
                assertThat(registrar.register(failedClient)).isTrue();
                throw new PulsarClientException(
                        "injected failure after client registration");
            }
            return new OneNetMqConsumer.SubscriptionConnection(
                    replacementClient, replacementConsumer);
        }, 10L, 20L);

        subject.start();

        assertThat(replacementReceiving.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(attempts).hasValue(2);
        verify(failedClient, times(1)).close();

        subject.stop();
        verify(replacementConsumer).close();
        verify(replacementClient).close();
    }

    @Test
    void receiveFailureClosesOldConnectionAndRetriesWithOneReplacement()
            throws Exception {
        AtomicInteger attempts = new AtomicInteger();
        @SuppressWarnings("unchecked")
        Consumer<byte[]> failedConsumer = mock(Consumer.class);
        PulsarClient failedClient = mock(PulsarClient.class);
        when(failedConsumer.receive()).thenThrow(
                new PulsarClientException("injected active connection loss"));

        CountDownLatch replacementReceiving = new CountDownLatch(1);
        Consumer<byte[]> replacementConsumer =
                blockingConsumer(replacementReceiving);
        PulsarClient replacementClient = mock(PulsarClient.class);
        subject = consumer(properties(), (properties, registrar) -> {
            if (attempts.incrementAndGet() == 1) {
                return new OneNetMqConsumer.SubscriptionConnection(
                        failedClient, failedConsumer);
            }
            return new OneNetMqConsumer.SubscriptionConnection(
                    replacementClient, replacementConsumer);
        }, 10L, 20L);

        subject.start();

        assertThat(replacementReceiving.await(2, TimeUnit.SECONDS)).isTrue();
        assertThat(attempts).hasValue(2);
        verify(failedConsumer).close();
        verify(failedClient).close();
        await().during(Duration.ofMillis(100))
                .atMost(Duration.ofSeconds(1))
                .untilAsserted(() -> assertThat(attempts).hasValue(2));

        subject.stop();
        verify(replacementConsumer).close();
        verify(replacementClient).close();
        assertThat(subject.isRunning()).isFalse();
    }

    @Test
    void stopWinningDuringStartPreflightPreventsWorkerAndConnection()
            throws Exception {
        CountDownLatch preflightEntered = new CountDownLatch(1);
        CountDownLatch releasePreflight = new CountDownLatch(1);
        AtomicInteger attempts = new AtomicInteger();
        @SuppressWarnings("unchecked")
        ObjectProvider<OneNetMessageHandler> handlers =
                mock(ObjectProvider.class);
        Environment environment = mock(Environment.class);
        when(environment.matchesProfiles("test")).thenAnswer(invocation -> {
            preflightEntered.countDown();
            releasePreflight.await();
            return false;
        });
        subject = new OneNetMqConsumer(
                properties(),
                handlers,
                new ObjectMapper(),
                environment,
                mock(OneNetDiagnosticLogger.class),
                (properties, registrar) -> {
                    attempts.incrementAndGet();
                    throw new PulsarClientException(
                            "connection must not run after stop");
                },
                10L,
                20L);

        Thread starter = new Thread(subject::start);
        starter.start();
        assertThat(preflightEntered.await(2, TimeUnit.SECONDS)).isTrue();
        subject.stop();
        releasePreflight.countDown();
        starter.join(2_000L);

        assertThat(starter.isAlive()).isFalse();
        assertThat(subject.isRunning()).isFalse();
        assertThat(attempts).hasValue(0);
    }

    private static OneNetMqConsumer consumer(
            OneNetSubscriptionProperties properties,
            OneNetMqConsumer.SubscriptionConnector connector,
            long initialRetryDelayMillis,
            long maximumRetryDelayMillis) {
        @SuppressWarnings("unchecked")
        ObjectProvider<OneNetMessageHandler> handlers =
                mock(ObjectProvider.class);
        Environment environment = mock(Environment.class);
        when(environment.matchesProfiles("test")).thenReturn(false);
        return new OneNetMqConsumer(
                properties,
                handlers,
                new ObjectMapper(),
                environment,
                mock(OneNetDiagnosticLogger.class),
                connector,
                initialRetryDelayMillis,
                maximumRetryDelayMillis);
    }

    private static OneNetSubscriptionProperties properties() {
        OneNetSubscriptionProperties properties =
                new OneNetSubscriptionProperties();
        properties.setEnabled(true);
        properties.setAccessId("test-access");
        properties.setSecretKey("test-secret");
        properties.setSubscriptionName("test-subscription");
        return properties;
    }

    private static Consumer<byte[]> blockingConsumer(
            CountDownLatch receiving) throws Exception {
        @SuppressWarnings("unchecked")
        Consumer<byte[]> consumer = mock(Consumer.class);
        when(consumer.receive()).thenAnswer(invocation -> {
            receiving.countDown();
            try {
                new CountDownLatch(1).await();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                throw new PulsarClientException(interrupted);
            }
            return null;
        });
        return consumer;
    }
}
