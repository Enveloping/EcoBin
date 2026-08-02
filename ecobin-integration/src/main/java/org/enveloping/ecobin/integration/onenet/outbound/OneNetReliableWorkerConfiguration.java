package org.enveloping.ecobin.integration.onenet.outbound;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Bean;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableScheduling;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

@Configuration(proxyBeanMethods = false)
@EnableScheduling
@ConditionalOnProperty(
        prefix = "ecobin.external",
        name = "mode",
        havingValue = "real")
@ConditionalOnProperty(
        prefix = "ecobin.operations.reliable",
        name = "workers-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class OneNetReliableWorkerConfiguration {

    @Bean(name = "oneNetInboundExecutor", destroyMethod = "shutdown")
    ExecutorService oneNetInboundExecutor(
            @Value("${ecobin.operations.reliable.iot-device.worker-count:2}")
            int workerCount,
            @Value("${ecobin.operations.reliable.iot-device.bounded-queue-capacity:64}")
            int queueCapacity) {
        return executor("onenet-inbound-", workerCount, queueCapacity);
    }

    @Bean(name = "oneNetOutboundExecutor", destroyMethod = "shutdown")
    ExecutorService oneNetOutboundExecutor(
            @Value("${ecobin.operations.reliable.iot-device.worker-count:2}")
            int workerCount,
            @Value("${ecobin.operations.reliable.iot-device.bounded-queue-capacity:64}")
            int queueCapacity) {
        return executor("onenet-outbound-", workerCount, queueCapacity);
    }

    private static ExecutorService executor(
            String prefix, int workers, int queueCapacity) {
        AtomicInteger sequence = new AtomicInteger();
        return new ThreadPoolExecutor(
                workers,
                workers,
                0L,
                TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(queueCapacity),
                runnable -> {
                    Thread thread = new Thread(
                            runnable,
                            prefix + sequence.incrementAndGet());
                    thread.setDaemon(false);
                    return thread;
                },
                new ThreadPoolExecutor.AbortPolicy());
    }
}
