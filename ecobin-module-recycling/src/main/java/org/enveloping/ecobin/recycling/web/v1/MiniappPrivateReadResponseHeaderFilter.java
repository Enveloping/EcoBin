package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.regex.Pattern;

/**
 * Prevents the current ordinary user's wallet and delivery history from being
 * cached, including responses that terminate in the security chain before a
 * controller is invoked.
 */
@Component
@Order(MiniappPrivateReadResponseHeaderFilter.FILTER_ORDER)
final class MiniappPrivateReadResponseHeaderFilter
        extends OncePerRequestFilter {

    static final int FILTER_ORDER = Ordered.HIGHEST_PRECEDENCE + 100;
    static final String REQUEST_ID_HEADER = "X-Request-Id";

    private static final String WALLET_PATH =
            "/api/v1/miniapp/me/wallet";
    private static final String WALLET_ENTRIES_PATH =
            "/api/v1/miniapp/me/wallet/entries";
    private static final String DELIVERY_ORDERS_PATH =
            "/api/v1/miniapp/me/delivery-orders";
    private static final Pattern DELIVERY_ORDER_DETAIL_PATH =
            Pattern.compile(
                    "^/api/v1/miniapp/me/delivery-orders/[^/]+$");

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain)
            throws ServletException, IOException {
        applyPrivateReadHeaders(request, response);
        filterChain.doFilter(request, response);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        if (!"GET".equals(request.getMethod())) {
            return true;
        }
        String path = applicationPath(request);
        return !WALLET_PATH.equals(path)
                && !WALLET_ENTRIES_PATH.equals(path)
                && !DELIVERY_ORDERS_PATH.equals(path)
                && !DELIVERY_ORDER_DETAIL_PATH.matcher(path).matches();
    }

    private static void applyPrivateReadHeaders(
            HttpServletRequest request,
            HttpServletResponse response) {
        response.setHeader(HttpHeaders.CACHE_CONTROL, "no-store");
        response.setHeader(
                REQUEST_ID_HEADER,
                TargetRequestIds.resolve(request));
    }

    private static String applicationPath(HttpServletRequest request) {
        String requestUri = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (contextPath != null
                && !contextPath.isEmpty()
                && requestUri.startsWith(contextPath)) {
            return requestUri.substring(contextPath.length());
        }
        return requestUri;
    }
}
