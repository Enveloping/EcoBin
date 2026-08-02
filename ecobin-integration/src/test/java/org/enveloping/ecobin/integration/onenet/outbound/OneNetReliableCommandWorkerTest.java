package org.enveloping.ecobin.integration.onenet.outbound;

import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.enveloping.ecobin.integration.onenet.OneNetDiagnosticLogger;
import org.enveloping.ecobin.operations.api.reliability.ReliableWorkerBatchResult;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.jdbc.BadSqlGrammarException;
import tools.jackson.databind.json.JsonMapper;

import java.sql.SQLSyntaxErrorException;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(OutputCaptureExtension.class)
class OneNetReliableCommandWorkerTest {

    @Test
    void synchronousWakeDrainsAvailableWorkAndReturns() {
        AtomicInteger calls = new AtomicInteger();
        var worker = new OneNetReliableCommandWorker(workerId ->
                calls.getAndIncrement() == 0
                        ? new ReliableWorkerBatchResult(1, 1, 0)
                        : new ReliableWorkerBatchResult(0, 0, 0),
                disabledDiagnosticLogger());

        worker.wake();

        assertEquals(2, calls.get());
    }

    @Test
    void databaseFailureLogsSafeStructuredDiagnostic(CapturedOutput output) {
        var sqlFailure = new SQLSyntaxErrorException(
                "denied secret-payload-marker", "42000", 1142);
        var translatedFailure = new BadSqlGrammarException(
                "claim device task",
                "SELECT secret-sql-marker",
                sqlFailure);
        var worker = new OneNetReliableCommandWorker(workerId -> {
            throw translatedFailure;
        }, disabledDiagnosticLogger());

        worker.poll();

        String logs = output.getOut();
        assertTrue(logs.contains("failureType=BadSqlGrammarException"));
        assertTrue(logs.contains("causeType=SQLSyntaxErrorException"));
        assertTrue(logs.contains("sqlState=42000"));
        assertTrue(logs.contains("vendorCode=1142"));
        assertFalse(logs.contains("secret-sql-marker"));
        assertFalse(logs.contains("secret-payload-marker"));
    }

    private static OneNetDiagnosticLogger disabledDiagnosticLogger() {
        return new OneNetDiagnosticLogger(
                new DiagnosticLoggingProperties(),
                new DiagnosticPayloadSanitizer(
                        JsonMapper.builder().build()));
    }
}
