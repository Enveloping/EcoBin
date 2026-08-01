package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MiniappPrivateReadResponseHeaderFilterTest {

    private static final String REQUEST_ID = "request-private-read";

    private final MiniappPrivateReadResponseHeaderFilter filter =
            new MiniappPrivateReadResponseHeaderFilter();

    @ParameterizedTest
    @CsvSource({
            "/api/v1/miniapp/me/wallet,200",
            "/api/v1/miniapp/me/wallet,401",
            "/api/v1/miniapp/me/wallet,500",
            "/api/v1/miniapp/me/wallet/entries,200",
            "/api/v1/miniapp/me/wallet/entries,400",
            "/api/v1/miniapp/me/wallet/entries,401",
            "/api/v1/miniapp/me/wallet/entries,500",
            "/api/v1/miniapp/me/delivery-orders,200",
            "/api/v1/miniapp/me/delivery-orders,401",
            "/api/v1/miniapp/me/delivery-orders/DO-20260730-000001,200",
            "/api/v1/miniapp/me/delivery-orders/DO-20260730-000001,404"
    })
    void protectsEveryOwnedReadResponse(String path, int status)
            throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest("GET", path);
        request.addHeader("X-Request-ID", REQUEST_ID);
        MockHttpServletResponse response =
                new MockHttpServletResponse();

        filter.doFilter(
                request,
                response,
                (servletRequest, servletResponse) ->
                        ((HttpServletResponse) servletResponse)
                                .setStatus(status));

        assertEquals(status, response.getStatus());
        assertEquals(
                "no-store",
                response.getHeader(HttpHeaders.CACHE_CONTROL));
        assertEquals(
                REQUEST_ID,
                response.getHeader(
                        MiniappPrivateReadResponseHeaderFilter
                                .REQUEST_ID_HEADER));
    }

    @ParameterizedTest
    @CsvSource({
            "GET,/api/v1/miniapp/me/phone-bindings",
            "POST,/api/v1/miniapp/me/wallet",
            "GET,/api/v1/miniapp/me/delivery-orders/one/extra"
    })
    void leavesUnrelatedRequestsUntouched(String method, String path)
            throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest(method, path);
        MockHttpServletResponse response =
                new MockHttpServletResponse();

        filter.doFilter(
                request,
                response,
                (servletRequest, servletResponse) -> {
                });

        assertNull(response.getHeader(HttpHeaders.CACHE_CONTROL));
        assertNull(response.getHeader(
                MiniappPrivateReadResponseHeaderFilter
                        .REQUEST_ID_HEADER));
    }

    @Test
    void runsBeforeTheSecurityFilterChain() {
        Order order = MiniappPrivateReadResponseHeaderFilter.class
                .getAnnotation(Order.class);

        assertEquals(
                MiniappPrivateReadResponseHeaderFilter.FILTER_ORDER,
                order.value());
        assertTrue(order.value() < -100);
    }
}
