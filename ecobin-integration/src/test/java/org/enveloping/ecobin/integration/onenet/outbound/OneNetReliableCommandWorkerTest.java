package org.enveloping.ecobin.integration.onenet.outbound;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.jdbc.BadSqlGrammarException;

import java.sql.SQLSyntaxErrorException;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(OutputCaptureExtension.class)
class OneNetReliableCommandWorkerTest {

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
        });

        worker.poll();

        String logs = output.getOut();
        assertTrue(logs.contains("failureType=BadSqlGrammarException"));
        assertTrue(logs.contains("causeType=SQLSyntaxErrorException"));
        assertTrue(logs.contains("sqlState=42000"));
        assertTrue(logs.contains("vendorCode=1142"));
        assertFalse(logs.contains("secret-sql-marker"));
        assertFalse(logs.contains("secret-payload-marker"));
    }
}
