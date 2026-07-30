package org.enveloping.ecobin.framework.web.logging;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingRequestWrapper;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

/**
 * 在认证过滤器之前记录 HTTP 到达事实，并在请求结束时记录状态、耗时和可选请求体。
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE + 10)
public final class HttpRequestLoggingFilter
        extends OncePerRequestFilter {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(HttpRequestLoggingFilter.class);

    private final HttpRequestLoggingProperties properties;
    private final HttpRequestLogSanitizer sanitizer;

    public HttpRequestLoggingFilter(
            HttpRequestLoggingProperties properties,
            ObjectMapper objectMapper) {
        this.properties = properties;
        this.sanitizer = new HttpRequestLogSanitizer(objectMapper);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!properties.isEnabled()) {
            return true;
        }
        String path = request.getRequestURI();
        return properties.getExcludedPathPrefixes().stream()
                .filter(prefix -> prefix != null && !prefix.isBlank())
                .anyMatch(path::startsWith);
    }

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain)
            throws ServletException, IOException {
        String requestId = TargetRequestIds.resolve(request);
        String safeRequestId =
                HttpRequestLogSanitizer.safeValue(requestId);
        String method =
                HttpRequestLogSanitizer.safeValue(request.getMethod());
        String path = HttpRequestLogSanitizer.safeValue(
                request.getRequestURI());
        String query = properties.isIncludeQueryString()
                ? sanitizer.query(request.getQueryString())
                : "<disabled>";
        String contentType = HttpRequestLogSanitizer.safeValue(
                request.getContentType());
        String idempotencyKey = HttpRequestLogSanitizer.safeValue(
                request.getHeader("Idempotency-Key"));
        String remoteAddress = HttpRequestLogSanitizer.safeValue(
                request.getRemoteAddr());
        long startedAt = System.nanoTime();
        boolean captureBody = properties.isIncludeRequestBody();
        ContentCachingRequestWrapper cachingRequest =
                captureBody
                        ? cache(request)
                        : null;
        HttpServletRequest requestToUse = cachingRequest == null
                ? request : cachingRequest;
        Throwable failure = null;

        response.setHeader("X-Request-Id", requestId);
        try (MDC.MDCCloseable ignored =
                     MDC.putCloseable("requestId", safeRequestId)) {
            LOGGER.info(
                    "HTTP_IN requestId={} method={} path={} query={} "
                            + "contentType={} contentLength={} "
                            + "idempotencyKey={} remoteAddress={}",
                    safeRequestId,
                    method,
                    path,
                    query,
                    contentType,
                    request.getContentLengthLong(),
                    idempotencyKey,
                    remoteAddress);
            try {
                filterChain.doFilter(requestToUse, response);
            } catch (ServletException
                     | IOException
                     | RuntimeException
                     | Error exception) {
                failure = exception;
                throw exception;
            } finally {
                long durationMs = TimeUnit.NANOSECONDS.toMillis(
                        System.nanoTime() - startedAt);
                int status = failure != null
                        && response.getStatus() < 400
                        ? 500 : response.getStatus();
                logCompletion(
                        safeRequestId,
                        method,
                        path,
                        status,
                        durationMs,
                        cachingRequest);
            }
        }
    }

    private void logCompletion(
            String requestId,
            String method,
            String path,
            int status,
            long durationMs,
            ContentCachingRequestWrapper request) {
        String message;
        Object[] arguments;
        if (request == null) {
            message = "HTTP_OUT requestId={} method={} path={} "
                    + "status={} durationMs={}";
            arguments = new Object[]{
                    requestId, method, path, status, durationMs
            };
        } else {
            byte[] content = request.getContentAsByteArray();
            boolean truncated = content.length
                    >= properties.getMaxPayloadLength()
                    || request.getContentLengthLong() > content.length;
            String body = sanitizer.body(
                    content,
                    request.getContentType(),
                    truncated);
            message = "HTTP_OUT requestId={} method={} path={} "
                    + "status={} durationMs={} body={}";
            arguments = new Object[]{
                    requestId, method, path, status, durationMs, body
            };
        }
        if (status >= 500) {
            LOGGER.warn(message, arguments);
        } else {
            LOGGER.info(message, arguments);
        }
    }

    private ContentCachingRequestWrapper cache(
            HttpServletRequest request) {
        if (request instanceof ContentCachingRequestWrapper caching) {
            return caching;
        }
        return new ContentCachingRequestWrapper(
                request, properties.getMaxPayloadLength());
    }
}
