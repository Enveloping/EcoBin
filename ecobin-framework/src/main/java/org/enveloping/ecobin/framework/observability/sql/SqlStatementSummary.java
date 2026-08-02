package org.enveloping.ecobin.framework.observability.sql;

import net.ttddyy.dsproxy.QueryInfo;
import net.ttddyy.dsproxy.proxy.ParameterSetOperation;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 从 SQL 模板提取不包含参数值的诊断摘要。 */
final class SqlStatementSummary {

    private static final Pattern BLOCK_COMMENT = Pattern.compile(
            "/\\*.*?\\*/", Pattern.DOTALL);
    private static final Pattern LINE_COMMENT = Pattern.compile(
            "--[^\\r\\n]*");
    private static final Pattern NUMBER_LITERAL = Pattern.compile(
            "(?<![A-Za-z0-9_?])[-+]?\\d+(?:\\.\\d+)?(?![A-Za-z0-9_?])");
    private static final Pattern HEX_LITERAL = Pattern.compile(
            "(?i)\\b0x[0-9a-f]+\\b");
    private static final Pattern TABLE = Pattern.compile(
            "(?i)\\b(?:from|join|update|into)\\s+"
                    + "((?:`[^`]+`|[A-Za-z0-9_]+)"
                    + "(?:\\.(?:`[^`]+`|[A-Za-z0-9_]+))?)");
    private static final Pattern OPERATION = Pattern.compile(
            "^([A-Za-z]+)");

    private SqlStatementSummary() {
    }

    static Summary from(
            List<QueryInfo> queryInfoList,
            int maxSqlLength,
            boolean includeParameterTypes) {
        List<String> queries = new ArrayList<>();
        Set<String> tables = new LinkedHashSet<>();
        Set<String> parameterTypes = new LinkedHashSet<>();
        int parameterSets = 0;
        int parameterCount = 0;
        for (QueryInfo queryInfo : queryInfoList) {
            String query = sanitizeSql(queryInfo.getQuery());
            queries.add(query);
            collectTables(query, tables);
            List<List<ParameterSetOperation>> parameters =
                    queryInfo.getParametersList();
            if (parameters == null) {
                continue;
            }
            parameterSets += parameters.size();
            for (List<ParameterSetOperation> parameterSet : parameters) {
                parameterCount = Math.max(
                        parameterCount, parameterSet.size());
                if (includeParameterTypes) {
                    for (ParameterSetOperation operation : parameterSet) {
                        parameterTypes.add(parameterType(operation));
                    }
                }
            }
        }
        String sql = String.join(" || ", queries);
        if (sql.length() > maxSqlLength) {
            sql = sql.substring(0, maxSqlLength)
                    + "…<truncated totalCharacters="
                    + sql.length() + ">";
        }
        String operation = queries.isEmpty()
                ? "UNKNOWN" : operation(queries.getFirst());
        return new Summary(
                operation,
                tables.isEmpty() ? "<unknown>" : String.join(",", tables),
                parameterSets,
                parameterCount,
                parameterTypes.isEmpty()
                        ? "<none>" : String.join(",", parameterTypes),
                sql.isBlank() ? "<empty>" : sql);
    }

    private static String sanitizeSql(String sql) {
        if (sql == null || sql.isBlank()) {
            return "";
        }
        String withoutComments = LINE_COMMENT.matcher(
                BLOCK_COMMENT.matcher(sql).replaceAll(" "))
                .replaceAll(" ");
        StringBuilder sanitized = new StringBuilder(
                withoutComments.length());
        boolean quoted = false;
        char quote = 0;
        for (int index = 0; index < withoutComments.length(); index++) {
            char character = withoutComments.charAt(index);
            if (!quoted && (character == '\'' || character == '"')) {
                quoted = true;
                quote = character;
                sanitized.append('?');
                continue;
            }
            if (quoted) {
                if (character == quote) {
                    if (index + 1 < withoutComments.length()
                            && withoutComments.charAt(index + 1) == quote) {
                        index++;
                    } else {
                        quoted = false;
                    }
                } else if (character == '\\'
                        && index + 1 < withoutComments.length()) {
                    index++;
                }
                continue;
            }
            sanitized.append(Character.isWhitespace(character)
                    ? ' ' : character);
        }
        String normalized = sanitized.toString()
                .replaceAll("\\s+", " ")
                .trim();
        return NUMBER_LITERAL.matcher(
                HEX_LITERAL.matcher(normalized).replaceAll("?"))
                .replaceAll("?");
    }

    private static void collectTables(
            String query, Set<String> tables) {
        Matcher matcher = TABLE.matcher(query);
        while (matcher.find()) {
            tables.add(matcher.group(1).replace("`", ""));
        }
    }

    private static String operation(String query) {
        Matcher matcher = OPERATION.matcher(query);
        return matcher.find()
                ? matcher.group(1).toUpperCase(Locale.ROOT)
                : "UNKNOWN";
    }

    private static String parameterType(
            ParameterSetOperation operation) {
        if (operation == null || operation.getMethod() == null) {
            return "unknown";
        }
        String methodName = operation.getMethod().getName();
        if ("setObject".equals(methodName)) {
            Object[] arguments = operation.getArgs();
            if (arguments != null && arguments.length > 1
                    && arguments[1] != null) {
                return arguments[1].getClass().getSimpleName();
            }
        }
        return methodName.startsWith("set")
                ? methodName.substring(3)
                : methodName;
    }

    record Summary(
            String operation,
            String tables,
            int parameterSets,
            int parameterCount,
            String parameterTypes,
            String sql) {
    }
}
