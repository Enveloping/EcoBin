package org.enveloping.ecobin.framework.web.v1;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

class TargetApiExceptionHandlerTest {

    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        mvc = standaloneSetup(new FailureController())
                .setControllerAdvice(new TargetApiExceptionHandler())
                .build();
    }

    @Test
    void duplicateDatabaseKeyIsAnActionableConflictWithoutLeakingSql()
            throws Exception {
        mvc.perform(get("/test/failures/duplicate")
                        .header("X-Request-ID", "req-duplicate"))
                .andExpect(status().isConflict())
                .andExpect(content().contentTypeCompatibleWith(
                        "application/problem+json"))
                .andExpect(jsonPath("$.code").value("DATA.DUPLICATE"))
                .andExpect(jsonPath("$.message").value(
                        "记录已存在或操作被重复提交，请刷新数据后重试"))
                .andExpect(jsonPath("$.requestId").value("req-duplicate"))
                .andExpect(jsonPath("$.retryable").value(false))
                .andExpect(jsonPath("$.details.failureCategory")
                        .value("DATABASE_CONSTRAINT"))
                .andExpect(content().string(
                        org.hamcrest.Matchers.not(
                                org.hamcrest.Matchers.containsString(
                                        "secret-value"))));
    }

    @Test
    void databaseConstraintViolationExplainsThatCurrentDataConflicts()
            throws Exception {
        mvc.perform(get("/test/failures/constraint")
                        .header("X-Request-ID", "req-constraint"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code")
                        .value("DATA.CONSTRAINT_CONFLICT"))
                .andExpect(jsonPath("$.message").value(
                        "当前数据状态与操作要求冲突，请刷新页面并核对关联记录后重试"))
                .andExpect(jsonPath("$.retryable").value(false))
                .andExpect(jsonPath("$.details.supportAction").value(
                        "若刷新后仍失败，请向管理员提供 requestId"));
    }

    @Test
    void temporaryDatabaseFailureIsServiceUnavailableAndRetryable()
            throws Exception {
        mvc.perform(get("/test/failures/timeout")
                        .header("X-Request-ID", "req-timeout"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code")
                        .value("DATA.TEMPORARILY_UNAVAILABLE"))
                .andExpect(jsonPath("$.message").value(
                        "数据库暂时不可用或响应超时，请稍后使用同一操作标识重试"))
                .andExpect(jsonPath("$.retryable").value(true))
                .andExpect(jsonPath("$.details.failureCategory")
                        .value("DATABASE_TEMPORARY"));
    }

    @Test
    void unexpectedFailureKeepsInternalsPrivateButGivesSupportDirection()
            throws Exception {
        mvc.perform(get("/test/failures/unexpected")
                        .header("X-Request-ID", "req-unexpected"))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code")
                        .value("COMMON.INTERNAL_ERROR"))
                .andExpect(jsonPath("$.message").value(
                        "服务器处理请求时发生未分类异常，请使用同一操作标识重试；若仍失败请联系管理员并提供请求编号"))
                .andExpect(jsonPath("$.retryable").value(true))
                .andExpect(jsonPath("$.details.failureCategory")
                        .value("UNEXPECTED_APPLICATION_ERROR"))
                .andExpect(content().string(
                        org.hamcrest.Matchers.not(
                                org.hamcrest.Matchers.containsString(
                                        "secret-value"))));
    }

    @Test
    void missingQueryParameterNamesTheMissingField() throws Exception {
        mvc.perform(get("/test/failures/required-parameter")
                        .header("X-Request-ID", "req-missing-parameter"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code")
                        .value("COMMON.MISSING_PARAMETER"))
                .andExpect(jsonPath("$.message").value(
                        "缺少必需请求参数：limit"))
                .andExpect(jsonPath("$.details.missingParameter")
                        .value("limit"))
                .andExpect(jsonPath("$.details.expectedType")
                        .value("Integer"));
    }

    @Test
    void malformedJsonIsDifferentFromAServiceFailure() throws Exception {
        mvc.perform(post("/test/failures/body")
                        .header("X-Request-ID", "req-json")
                        .contentType("application/json")
                        .content("{not-json"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code")
                        .value("COMMON.MALFORMED_REQUEST_BODY"))
                .andExpect(jsonPath("$.message").value(
                        "请求体不是有效的 JSON，或字段类型不符合接口契约"))
                .andExpect(jsonPath("$.details.failureCategory")
                        .value("MALFORMED_REQUEST_BODY"));
    }

    @RestController
    private static final class FailureController {

        @GetMapping("/test/failures/duplicate")
        void duplicate() {
            throw new DuplicateKeyException(
                    "Duplicate entry 'secret-value' for key 'secret-key'");
        }

        @GetMapping("/test/failures/constraint")
        void constraint() {
            throw new DataIntegrityViolationException(
                    "SQL constraint leaked secret-value");
        }

        @GetMapping("/test/failures/timeout")
        void timeout() {
            throw new QueryTimeoutException(
                    "SQL query leaked secret-value");
        }

        @GetMapping("/test/failures/unexpected")
        void unexpected() {
            throw new IllegalStateException(
                    "internal secret-value must not reach the client");
        }

        @GetMapping("/test/failures/required-parameter")
        void requiredParameter(@RequestParam Integer limit) {
        }

        @PostMapping("/test/failures/body")
        void body(@RequestBody TestBody body) {
        }
    }

    private record TestBody(String value) {
    }
}
