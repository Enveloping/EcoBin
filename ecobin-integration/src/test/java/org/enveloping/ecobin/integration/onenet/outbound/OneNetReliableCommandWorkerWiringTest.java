package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.operations.api.reliability.ReliableDeviceCommandWorkerPort;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.core.env.MapPropertySource;
import tools.jackson.databind.json.JsonMapper;

import java.util.Map;
import java.util.concurrent.Executor;

import static org.junit.jupiter.api.Assertions.assertNotNull;

class OneNetReliableCommandWorkerWiringTest {

    @Test
    void springCanSelectTheProductionConstructor() {
        try (var context = new AnnotationConfigApplicationContext()) {
            context.getEnvironment().getPropertySources().addFirst(
                    new MapPropertySource(
                            "worker-wiring-test",
                            Map.of(
                                    "ecobin.external.mode", "real",
                                    "ecobin.operations.reliable.workers-enabled", "true",
                                    "ecobin.operations.reliable.iot-device.worker-count", "1")));
            context.registerBean(
                    ReliableDeviceCommandWorkerPort.class,
                    () -> workerId -> new ReliableWorkerBatchResult(0, 0, 0));
            context.registerBean(
                    OneNetDiagnosticLogger.class,
                    OneNetReliableCommandWorkerWiringTest::diagnosticLogger);
            context.registerBean(
                    "oneNetOutboundExecutor",
                    Executor.class,
                    () -> Runnable::run);
            context.register(OneNetReliableCommandWorker.class);

            context.refresh();

            assertNotNull(context.getBean(OneNetReliableCommandWorker.class));
        }
    }

    private static OneNetDiagnosticLogger diagnosticLogger() {
        return new OneNetDiagnosticLogger(
                new DiagnosticLoggingProperties(),
                new DiagnosticPayloadSanitizer(JsonMapper.builder().build()));
    }
}
