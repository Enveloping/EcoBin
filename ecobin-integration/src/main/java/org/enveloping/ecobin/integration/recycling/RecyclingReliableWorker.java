package org.enveloping.ecobin.integration.recycling;

import org.enveloping.ecobin.operations.api.reliability.ReliableRecyclingTaskWorkerPort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.UUID;

/** 到期投递自动审核任务的数据库轮询适配器。 */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.operations.reliable",
        name = "workers-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class RecyclingReliableWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(RecyclingReliableWorker.class);

    private final ReliableRecyclingTaskWorkerPort runner;
    private final String workerId = "recycling-" + UUID.randomUUID();

    public RecyclingReliableWorker(
            ReliableRecyclingTaskWorkerPort runner) {
        this.runner = runner;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.reliable.recycling.poll-interval:1s}")
    public void poll() {
        try {
            int handled = 0;
            while (handled < 16 && runner.runNext(workerId)) {
                handled++;
            }
            if (handled > 0) {
                LOGGER.info(
                        "recycling reliable tasks handled={}",
                        handled);
            }
        } catch (RuntimeException failure) {
            LOGGER.warn(
                    "recycling reliable worker recovered from {}",
                    failure.getClass().getSimpleName());
        }
    }
}
