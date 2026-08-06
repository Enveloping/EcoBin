package org.enveloping.ecobin.framework.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.dao.ConcurrencyFailureException;
import org.springframework.dao.DataAccessException;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.IncorrectResultSizeDataAccessException;
import org.springframework.dao.InvalidDataAccessResourceUsageException;
import org.springframework.dao.TransientDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

import java.sql.SQLException;
import java.util.LinkedHashMap;
import java.util.Map;

@Order(Ordered.HIGHEST_PRECEDENCE)
@RestControllerAdvice(basePackages = "org.enveloping.ecobin")
public class TargetApiExceptionHandler {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(TargetApiExceptionHandler.class);

    @ExceptionHandler(TargetApiException.class)
    public ResponseEntity<TargetProblemDetail> handleTarget(
            TargetApiException exception,
            HttpServletRequest request) {
        return ResponseEntity.status(exception.status())
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new TargetProblemDetail(
                        exception.code(),
                        exception.getMessage(),
                        TargetRequestIds.resolve(request),
                        exception.retryable(),
                        exception.details()));
    }

    @ExceptionHandler({
            MethodArgumentNotValidException.class,
            MissingRequestHeaderException.class,
            MissingServletRequestParameterException.class,
            MethodArgumentTypeMismatchException.class,
            HttpMessageNotReadableException.class
    })
    public ResponseEntity<TargetProblemDetail> handleInvalidRequest(
            Exception exception,
            HttpServletRequest request) {
        Map<String, Object> details = new LinkedHashMap<>();
        if (exception instanceof MethodArgumentNotValidException validation) {
            for (FieldError error
                    : validation.getBindingResult().getFieldErrors()) {
                details.putIfAbsent(
                        error.getField(), error.getDefaultMessage());
            }
            return problem(
                    request,
                    HttpStatus.BAD_REQUEST,
                    "COMMON.VALIDATION_FAILED",
                    "请求字段校验失败，请根据 details 中的字段提示修正后重试",
                    false,
                    details);
        } else if (exception instanceof MissingRequestHeaderException missing) {
            details.put("missingHeader", missing.getHeaderName());
            return problem(
                    request,
                    HttpStatus.BAD_REQUEST,
                    "COMMON.MISSING_HEADER",
                    "缺少必需请求头：" + missing.getHeaderName(),
                    false,
                    details);
        } else if (exception
                instanceof MissingServletRequestParameterException missing) {
            details.put("missingParameter", missing.getParameterName());
            details.put("expectedType", missing.getParameterType());
            return problem(
                    request,
                    HttpStatus.BAD_REQUEST,
                    "COMMON.MISSING_PARAMETER",
                    "缺少必需请求参数：" + missing.getParameterName(),
                    false,
                    details);
        } else if (exception
                instanceof MethodArgumentTypeMismatchException mismatch) {
            details.put("parameter", mismatch.getName());
            if (mismatch.getRequiredType() != null) {
                details.put("expectedType",
                        mismatch.getRequiredType().getSimpleName());
            }
            return problem(
                    request,
                    HttpStatus.BAD_REQUEST,
                    "COMMON.PARAMETER_TYPE_MISMATCH",
                    "请求参数类型不正确：" + mismatch.getName(),
                    false,
                    details);
        } else if (exception instanceof HttpMessageNotReadableException) {
            details.put("failureCategory", "MALFORMED_REQUEST_BODY");
            return problem(
                    request,
                    HttpStatus.BAD_REQUEST,
                    "COMMON.MALFORMED_REQUEST_BODY",
                    "请求体不是有效的 JSON，或字段类型不符合接口契约",
                    false,
                    details);
        }
        return invalidRequest(request, details);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<TargetProblemDetail> handleIllegalArgument(
            IllegalArgumentException exception,
            HttpServletRequest request) {
        return problem(
                request,
                HttpStatus.BAD_REQUEST,
                "COMMON.INVALID_ARGUMENT",
                "请求参数无法解析或违反接口约束，请核对字段格式后重试",
                false,
                details(
                        "INVALID_ARGUMENT",
                        "请依据接口契约核对参数类型、格式和取值范围"));
    }

    /**
     * Translate Spring's persistence exceptions into safe, actionable API
     * failures. SQL text, constraint names and bound values stay exclusively
     * in the server log; callers receive only a stable category and request ID.
     */
    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<TargetProblemDetail> handleDataAccess(
            DataAccessException exception,
            HttpServletRequest request) {
        String requestId = TargetRequestIds.resolve(request);
        SQLException sqlException = findSqlException(exception);
        LOGGER.error(
                "Target API persistence failure requestId={} type={} "
                        + "origin={} sqlState={} vendorCode={}",
                requestId,
                exception.getClass().getName(),
                failureOrigin(exception),
                sqlException == null ? "<none>" : sqlException.getSQLState(),
                sqlException == null ? "<none>"
                        : sqlException.getErrorCode(),
                exception);
        if (exception instanceof DuplicateKeyException) {
            return problem(
                    request,
                    HttpStatus.CONFLICT,
                    "DATA.DUPLICATE",
                    "记录已存在或操作被重复提交，请刷新数据后重试",
                    false,
                    details(
                            "DATABASE_CONSTRAINT",
                            "请刷新页面并核对记录是否已经创建"));
        }
        if (exception instanceof DataIntegrityViolationException) {
            return problem(
                    request,
                    HttpStatus.CONFLICT,
                    "DATA.CONSTRAINT_CONFLICT",
                    "当前数据状态与操作要求冲突，请刷新页面并核对关联记录后重试",
                    false,
                    details(
                            "DATABASE_CONSTRAINT",
                            "若刷新后仍失败，请向管理员提供 requestId"));
        }
        if (exception instanceof IncorrectResultSizeDataAccessException) {
            return problem(
                    request,
                    HttpStatus.CONFLICT,
                    "DATA.REQUIRED_RECORD_INCONSISTENT",
                    "操作依赖的关联记录缺失或不唯一，已停止本次操作以保护数据",
                    false,
                    details(
                            "DATABASE_RELATIONSHIP",
                            "请刷新页面；若仍失败，请向管理员提供 requestId"));
        }
        if (exception instanceof ConcurrencyFailureException) {
            return problem(
                    request,
                    HttpStatus.CONFLICT,
                    "DATA.CONCURRENT_MODIFICATION",
                    "数据正在被其他操作修改，请稍后重试；写操作请复用同一操作标识",
                    true,
                    details(
                            "DATABASE_CONCURRENCY",
                            "请刷新最新版本后重试"));
        }
        if (exception instanceof TransientDataAccessException
                || exception instanceof DataAccessResourceFailureException) {
            return problem(
                    request,
                    HttpStatus.SERVICE_UNAVAILABLE,
                    "DATA.TEMPORARILY_UNAVAILABLE",
                    "数据库暂时不可用或响应超时，请稍后使用同一操作标识重试",
                    true,
                    details(
                            "DATABASE_TEMPORARY",
                            "稍后重试；若持续失败，请向管理员提供 requestId"));
        }
        if (exception instanceof InvalidDataAccessResourceUsageException) {
            return problem(
                    request,
                    HttpStatus.INTERNAL_SERVER_ERROR,
                    "DATA.SCHEMA_OR_QUERY_ERROR",
                    "服务端数据库结构或查询不兼容，请联系管理员并提供请求编号",
                    false,
                    details(
                            "DATABASE_SCHEMA_OR_QUERY",
                            "请检查数据库迁移版本和服务端日志"));
        }
        return problem(
                request,
                HttpStatus.INTERNAL_SERVER_ERROR,
                "DATA.ACCESS_ERROR",
                "服务端数据库操作失败，无法安全确认本次操作结果，请使用同一操作标识重试；若仍失败请联系管理员并提供请求编号",
                true,
                details(
                        "DATABASE_UNCLASSIFIED",
                        "请根据 requestId 检索服务端完整错误日志"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<TargetProblemDetail> handleUnexpected(
            Exception exception,
            HttpServletRequest request) {
        LOGGER.error(
                "Unhandled target API exception requestId={} type={} origin={}",
                TargetRequestIds.resolve(request),
                exception.getClass().getName(),
                failureOrigin(exception),
                exception);
        return problem(
                request,
                HttpStatus.INTERNAL_SERVER_ERROR,
                "COMMON.INTERNAL_ERROR",
                "服务器处理请求时发生未分类异常，请使用同一操作标识重试；若仍失败请联系管理员并提供请求编号",
                true,
                details(
                        "UNEXPECTED_APPLICATION_ERROR",
                        "请根据 requestId 检索服务端完整错误日志"));
    }

    private static ResponseEntity<TargetProblemDetail> invalidRequest(
            HttpServletRequest request,
            Map<String, Object> details) {
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new TargetProblemDetail(
                        "COMMON.INVALID_REQUEST",
                        "请求字段不符合接口契约",
                        TargetRequestIds.resolve(request),
                        false,
                        details));
    }

    private static ResponseEntity<TargetProblemDetail> problem(
            HttpServletRequest request,
            HttpStatus status,
            String code,
            String message,
            boolean retryable,
            Map<String, Object> details) {
        return ResponseEntity.status(status)
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(new TargetProblemDetail(
                        code,
                        message,
                        TargetRequestIds.resolve(request),
                        retryable,
                        details));
    }

    private static Map<String, Object> details(
            String failureCategory,
            String supportAction) {
        return Map.of(
                "failureCategory", failureCategory,
                "supportAction", supportAction);
    }

    private static String failureOrigin(Throwable failure) {
        StackTraceElement[] stack = failure.getStackTrace();
        for (StackTraceElement frame : stack) {
            if (frame.getClassName().startsWith(
                    "org.enveloping.ecobin")) {
                return frame.toString();
            }
        }
        return stack.length == 0 ? "<unknown>" : stack[0].toString();
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
}
