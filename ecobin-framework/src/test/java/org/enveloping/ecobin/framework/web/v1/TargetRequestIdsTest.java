package org.enveloping.ecobin.framework.web.v1;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

import static org.assertj.core.api.Assertions.assertThat;

class TargetRequestIdsTest {

    @Test
    void keepsSafeClientRequestIdForCorrelation() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Request-ID", "web:request-01");

        assertThat(TargetRequestIds.resolve(request))
                .isEqualTo("web:request-01");
        assertThat(TargetRequestIds.resolve(request))
                .isEqualTo("web:request-01");
    }

    @Test
    void replacesUnsafeRequestIdInsteadOfWritingItToLogsOrHeaders() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Request-ID", "forged\r\nlog-entry");

        assertThat(TargetRequestIds.resolve(request))
                .matches("[0-9a-f-]{36}")
                .doesNotContain("\r")
                .doesNotContain("\n");
    }
}
