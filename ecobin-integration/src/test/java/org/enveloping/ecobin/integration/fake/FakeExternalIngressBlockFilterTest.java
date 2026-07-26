package org.enveloping.ecobin.integration.fake;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FakeExternalIngressBlockFilterTest {

    @Test
    void blocksLegacyDeviceIngressBeforeItReachesControllers() throws Exception {
        var filter = new FakeExternalIngressBlockFilter(true);
        var request = new MockHttpServletRequest(
                "POST", "/api/iot/delivery/complete");
        var response = new MockHttpServletResponse();
        var invoked = new AtomicBoolean(false);

        filter.doFilter(request, response, (req, res) -> invoked.set(true));

        assertEquals(503, response.getStatus());
        assertFalse(invoked.get());
    }

    @Test
    void blocksDeviceIngressWhenApplicationHasContextPath() throws Exception {
        var filter = new FakeExternalIngressBlockFilter(true);
        var request = new MockHttpServletRequest(
                "POST", "/ctx/api/iot/delivery/complete");
        request.setContextPath("/ctx");
        request.setServletPath("/api/iot/delivery/complete");
        var response = new MockHttpServletResponse();
        var invoked = new AtomicBoolean(false);

        filter.doFilter(request, response, (req, res) -> invoked.set(true));

        assertEquals(503, response.getStatus());
        assertFalse(invoked.get());
    }

    @Test
    void permitsOrdinaryInternalApiTraffic() throws Exception {
        var filter = new FakeExternalIngressBlockFilter(true);
        var request = new MockHttpServletRequest(
                "GET", "/api/system/tenant/me");
        var response = new MockHttpServletResponse();
        var invoked = new AtomicBoolean(false);

        filter.doFilter(request, response, (req, res) -> invoked.set(true));

        assertTrue(invoked.get());
    }
}
