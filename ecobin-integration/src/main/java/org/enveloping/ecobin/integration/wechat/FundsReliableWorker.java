package org.enveloping.ecobin.integration.wechat;

import org.enveloping.ecobin.operations.api.reliability.ReliableFundsInboxWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableFundsTaskWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.UUID;

/**
 * 微信资金任务恢复轮询器。业务状态和重试真相在数据库中，进程重启后继续领取原单。
 */
@Component
@ConditionalOnProperty(
        prefix = "ecobin.operations.reliable",
        name = "workers-enabled",
        havingValue = "true",
        matchIfMissing = true)
public class FundsReliableWorker {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(FundsReliableWorker.class);

    private final ReliableFundsTaskWorkerPort runner;
    private final ReliableFundsInboxWorkerPort inboxRunner;
    private final String workerId = "funds-wechat-" + UUID.randomUUID();

    public FundsReliableWorker(
            ReliableFundsTaskWorkerPort runner,
            ReliableFundsInboxWorkerPort inboxRunner) {
        this.runner = runner;
        this.inboxRunner = inboxRunner;
    }

    @Scheduled(
            fixedDelayString =
                    "${ecobin.operations.reliable.funds-wechat.poll-interval:1s}")
    public void poll() {
        try {
            int handled = 0;
            ReliableWorkerBatchResult inbox = inboxRunner.runBatch(
                    workerId + "-inbox");
            while (handled < 32 && runner.runNext(workerId)) {
                handled++;
            }
            if (handled > 0 || inbox.claimed() > 0) {
                LOGGER.info(
                        "funds reliable tasks handled={} inboxClaimed={} inboxApplied={} inboxFailed={}",
                        handled, inbox.claimed(), inbox.accepted(), inbox.failed());
            }
        } catch (RuntimeException failure) {
            LOGGER.warn(
                    "funds reliable worker recovered from {}",
                    failure.getClass().getSimpleName());
        }
    }
}
