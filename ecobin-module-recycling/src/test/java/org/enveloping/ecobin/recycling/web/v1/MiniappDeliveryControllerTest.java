package org.enveloping.ecobin.recycling.web.v1;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.framework.web.v1.TargetApiExceptionHandler;
import org.enveloping.ecobin.recycling.application.delivery.StartDeliverySessionService;
import org.enveloping.ecobin.recycling.application.deliveryorder.DeliveryOrderQueryService;
import org.enveloping.ecobin.recycling.application.deliveryquery.MiniappDeliveryQueryService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.web.servlet.MockMvc;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import static org.springframework.test.web.servlet.setup.MockMvcBuilders.standaloneSetup;

@ExtendWith(MockitoExtension.class)
class MiniappDeliveryControllerTest {

    private static final String REQUEST_ID = "request-1";

    @Mock
    private StartDeliverySessionService startService;
    @Mock
    private MiniappDeliveryQueryService queryService;
    @Mock
    private DeliveryOrderQueryService orderQueryService;

    private MiniappDeliveryController controller;

    @BeforeEach
    void setUp() {
        controller = new MiniappDeliveryController(
                startService,
                queryService,
                orderQueryService);
    }

    @Test
    void deliveryOrderPageDelegatesToTheOwnedQuery() {
        MockHttpServletRequest request = request();

        var envelope = controller.deliveryOrders(
                null,
                20,
                "PENDING",
                request);

        assertThat(envelope).isNotNull();
        assertThat(envelope.requestId()).isEqualTo(REQUEST_ID);
        verify(orderQueryService).miniappOrders(
                null,
                20,
                "PENDING");
    }

    @Test
    void deliveryOrderDetailDelegatesToTheOwnedQuery() {
        MockHttpServletRequest request = request();

        var envelope = controller.deliveryOrder(
                "DO-20260730-000001",
                request);

        assertThat(envelope).isNotNull();
        assertThat(envelope.requestId()).isEqualTo(REQUEST_ID);
        verify(orderQueryService).miniappOrder(
                "DO-20260730-000001");
    }

    @Test
    void missingOwnedOrderIsNotCacheable() throws Exception {
        when(orderQueryService.miniappOrder("DO-20260730-000404"))
                .thenThrow(new TargetApiException(
                        404,
                        "RESOURCE.NOT_FOUND",
                        "投递订单不存在"));
        MockMvc mvc = standaloneSetup(controller)
                .setControllerAdvice(new TargetApiExceptionHandler())
                .addFilters(new MiniappPrivateReadResponseHeaderFilter())
                .build();

        mvc.perform(get(
                        "/api/v1/miniapp/me/delivery-orders/"
                                + "DO-20260730-000404")
                        .header("X-Request-ID", "req-order-missing"))
                .andExpect(status().isNotFound())
                .andExpect(header().string(
                        HttpHeaders.CACHE_CONTROL,
                        "no-store"))
                .andExpect(header().string(
                        MiniappPrivateReadResponseHeaderFilter
                                .REQUEST_ID_HEADER,
                        "req-order-missing"))
                .andExpect(jsonPath("$.requestId")
                        .value("req-order-missing"));
    }

    private static MockHttpServletRequest request() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("X-Request-ID", REQUEST_ID);
        return request;
    }
}
