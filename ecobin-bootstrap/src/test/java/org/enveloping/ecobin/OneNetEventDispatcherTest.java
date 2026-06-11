package org.enveloping.ecobin;

import org.enveloping.ecobin.business.dto.CleanGrossRequest;
import org.enveloping.ecobin.business.dto.CleanTareRequest;
import org.enveloping.ecobin.business.dto.DeliveryReportRequest;
import org.enveloping.ecobin.business.onenet.OneNetEventDispatcher;
import org.enveloping.ecobin.business.service.CleanOrderService;
import org.enveloping.ecobin.business.service.DeliveryOrderService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

/**
 * OneNet 事件分发器路由单测：喂入构造的解密后 JSON，断言映射到正确的 Service 入口、参数解析正确，
 * 兼容 {@code value} 包裹/未包裹两种 thingEvent 形态，未知 msgType 安全跳过。无需 Spring/凭证。
 */
class OneNetEventDispatcherTest {

    private CleanOrderService cleanOrderService;
    private DeliveryOrderService deliveryOrderService;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        cleanOrderService = mock(CleanOrderService.class);
        deliveryOrderService = mock(DeliveryOrderService.class);
        dispatcher = new OneNetEventDispatcher(cleanOrderService, deliveryOrderService,
                JsonMapper.builder().build());
    }

    @Test
    void cleanGross_wrappedInValue_routesToReportGross() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0001",
                "params":{"cleanGross":{"value":{"cleanOrderId":123,"weight":12.5},"time":1700000000000}}}}""";

        dispatcher.handle(json);

        ArgumentCaptor<CleanGrossRequest> captor = ArgumentCaptor.forClass(CleanGrossRequest.class);
        verify(cleanOrderService).reportGross(captor.capture());
        CleanGrossRequest req = captor.getValue();
        assertThat(req.getSn()).isEqualTo("EcoBin-SN-0001");
        assertThat(req.getCleanOrderId()).isEqualTo(123L);
        assertThat(req.getWeight()).isEqualByComparingTo(new BigDecimal("12.5"));
    }

    @Test
    void cleanTare_unwrapped_routesToReportTare() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0002",
                "params":{"cleanTare":{"cleanOrderId":456,"weight":0.30}}}}""";

        dispatcher.handle(json);

        ArgumentCaptor<CleanTareRequest> captor = ArgumentCaptor.forClass(CleanTareRequest.class);
        verify(cleanOrderService).reportTare(captor.capture());
        CleanTareRequest req = captor.getValue();
        assertThat(req.getSn()).isEqualTo("EcoBin-SN-0002");
        assertThat(req.getCleanOrderId()).isEqualTo(456L);
        assertThat(req.getWeight()).isEqualByComparingTo(new BigDecimal("0.30"));
    }

    @Test
    void deliveryComplete_routesToCompleteDelivery() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0003",
                "params":{"deliveryComplete":{"value":{"deliveryToken":"TK-9","weight":3.2,"wasteType1":1,"wasteType2":11}}}}}""";

        dispatcher.handle(json);

        ArgumentCaptor<DeliveryReportRequest> captor = ArgumentCaptor.forClass(DeliveryReportRequest.class);
        verify(deliveryOrderService).completeDelivery(captor.capture());
        DeliveryReportRequest req = captor.getValue();
        assertThat(req.getSn()).isEqualTo("EcoBin-SN-0003");
        assertThat(req.getDeliveryToken()).isEqualTo("TK-9");
        assertThat(req.getWeight()).isEqualByComparingTo(new BigDecimal("3.2"));
        assertThat(req.getWasteType1()).isEqualTo(1);
        assertThat(req.getWasteType2()).isEqualTo(11);
    }

    @Test
    void unknownMsgType_isSkipped() {
        String json = """
                {"msgType":"thingProperty","subData":{"deviceName":"EcoBin-SN-0004","params":{"voltage":{"value":12.0}}}}""";

        dispatcher.handle(json);

        verifyNoInteractions(cleanOrderService, deliveryOrderService);
    }
}
