package org.enveloping.ecobin.framework.observability.sql;

import net.ttddyy.dsproxy.ExecutionInfo;
import net.ttddyy.dsproxy.QueryInfo;
import net.ttddyy.dsproxy.listener.QueryExecutionListener;
import org.enveloping.ecobin.framework.observability.DiagnosticLoggingProperties;
import org.enveloping.ecobin.framework.observability.DiagnosticPayloadSanitizer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.sql.SQLException;
import java.sql.Statement;
import java.util.List;

/** 逐次记录不含参数值的 SQL 执行摘要。 */
@Component
public final class SqlQuerySummaryListener
        implements QueryExecutionListener {

    private static final Logger LOGGER = LoggerFactory.getLogger(
            "org.enveloping.ecobin.diagnostics.sql");

    private final DiagnosticLoggingProperties properties;
    private final DiagnosticPayloadSanitizer sanitizer;

    public SqlQuerySummaryListener(
            DiagnosticLoggingProperties properties,
            DiagnosticPayloadSanitizer sanitizer) {
        this.properties = properties;
        this.sanitizer = sanitizer;
    }

    @Override
    public void beforeQuery(
            ExecutionInfo executionInfo,
            List<QueryInfo> queryInfoList) {
        // 只在执行结束时记录一次，避免请求/结果拆成两条难以关联。
    }

    @Override
    public void afterQuery(
            ExecutionInfo executionInfo,
            List<QueryInfo> queryInfoList) {
        if (!properties.getSql().isEnabled()) {
            return;
        }
        SqlStatementSummary.Summary summary =
                SqlStatementSummary.from(
                        queryInfoList,
                        properties.getSql().getMaxSqlLength(),
                        properties.getSql().isIncludeParameterTypes());
        SQLException sqlException = findSqlException(
                executionInfo.getThrowable());
        String outcome = executionInfo.isSuccess()
                ? "SUCCESS" : "FAILED";
        String message =
                "SQL_SUMMARY operation={} tables={} durationMs={} "
                        + "queryCount={} batchSize={} parameterSets={} "
                        + "parameterCount={} parameterTypes={} "
                        + "affectedRows={} outcome={} sqlState={} "
                        + "vendorCode={} sql={}";
        Object[] arguments = new Object[]{
                summary.operation(),
                summary.tables(),
                executionInfo.getElapsedTime(),
                queryInfoList.size(),
                executionInfo.isBatch()
                        ? executionInfo.getBatchSize() : 0,
                summary.parameterSets(),
                summary.parameterCount(),
                properties.getSql().isIncludeParameterTypes()
                        ? summary.parameterTypes() : "<disabled>",
                affectedRows(executionInfo.getResult()),
                outcome,
                sqlException == null
                        ? "<none>" : safe(sqlException.getSQLState()),
                sqlException == null
                        ? "<none>" : sqlException.getErrorCode(),
                summary.sql()
        };
        if (!executionInfo.isSuccess()) {
            logFailure(message, arguments, executionInfo.getThrowable());
        } else if (executionInfo.getElapsedTime()
                >= properties.getSql().getSlowQueryThresholdMs()) {
            LOGGER.warn(message, arguments);
        } else {
            LOGGER.info(message, arguments);
        }
    }

    private void logFailure(
            String message,
            Object[] arguments,
            Throwable failure) {
        if (failure != null
                && properties.getSql().isIncludeStackTrace()) {
            Object[] withFailure = new Object[arguments.length + 1];
            System.arraycopy(
                    arguments, 0, withFailure, 0, arguments.length);
            withFailure[arguments.length] = sanitizer.throwable(
                    failure, 2_048);
            LOGGER.error(message, withFailure);
        } else {
            LOGGER.error(message, arguments);
        }
    }

    private String safe(String value) {
        return sanitizer.text(value, 128);
    }

    private static SQLException findSqlException(Throwable failure) {
        Throwable current = failure;
        for (int depth = 0; current != null && depth < 16; depth++) {
            if (current instanceof SQLException sqlException) {
                return sqlException;
            }
            if (current.getCause() == current) {
                break;
            }
            current = current.getCause();
        }
        return null;
    }

    private static String affectedRows(Object result) {
        if (result instanceof Number number) {
            return number.toString();
        }
        if (result instanceof int[] values) {
            return batchAffectedRows(values);
        }
        if (result instanceof long[] values) {
            long total = 0;
            for (long value : values) {
                if (value == Statement.SUCCESS_NO_INFO) {
                    return "<unknown>";
                }
                if (value >= 0) {
                    total += value;
                }
            }
            return Long.toString(total);
        }
        return "<not-reported>";
    }

    private static String batchAffectedRows(int[] values) {
        long total = 0;
        for (int value : values) {
            if (value == Statement.SUCCESS_NO_INFO) {
                return "<unknown>";
            }
            if (value >= 0) {
                total += value;
            }
        }
        return Long.toString(total);
    }
}
