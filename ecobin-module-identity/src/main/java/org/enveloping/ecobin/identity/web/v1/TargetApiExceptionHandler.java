package org.enveloping.ecobin.identity.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.http.converter.HttpMessageNotReadableException;

import java.util.LinkedHashMap;
import java.util.Map;

@Order(Ordered.HIGHEST_PRECEDENCE)
@RestControllerAdvice(basePackages =
        "org.enveloping.ecobin.identity.web.v1")
public class TargetApiExceptionHandler {

    @ExceptionHandler(TargetApiException.class)
    public ResponseEntity<TargetProblemDetail> handleTarget(
            TargetApiException exception,
            HttpServletRequest request) {
        return ResponseEntity.status(exception.status())
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(
                new TargetProblemDetail(
                        exception.code(),
                        exception.getMessage(),
                        TargetRequestIds.resolve(request),
                        exception.retryable(),
                        exception.details()));
    }

    @ExceptionHandler({
            MethodArgumentNotValidException.class,
            MissingRequestHeaderException.class,
            MethodArgumentTypeMismatchException.class,
            HttpMessageNotReadableException.class,
            IllegalArgumentException.class
    })
    public ResponseEntity<TargetProblemDetail> handleInvalidRequest(
            Exception exception,
            HttpServletRequest request) {
        Map<String, Object> details = new LinkedHashMap<>();
        if (exception instanceof MethodArgumentNotValidException validation) {
            for (FieldError error : validation.getBindingResult().getFieldErrors()) {
                details.putIfAbsent(error.getField(), error.getDefaultMessage());
            }
        }
        return ResponseEntity.status(HttpStatus.BAD_REQUEST)
                .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(
                new TargetProblemDetail(
                        "COMMON.INVALID_REQUEST",
                        "请求字段不符合接口契约",
                        TargetRequestIds.resolve(request),
                        false,
                        details));
    }
}
