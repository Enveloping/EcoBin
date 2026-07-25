package org.enveloping.ecobin;

import org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcher;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleanGrossCommand;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleaningEventPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyCleanTareCommand;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryEventPort;
import org.enveloping.ecobin.recycling.api.legacy.LegacyDeliveryReportCommand;
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

    private LegacyCleaningEventPort cleaningEventPort;
    private LegacyDeliveryEventPort deliveryEventPort;
    private OneNetEventDispatcher dispatcher;

    @BeforeEach
    void setUp() {
        cleaningEventPort = mock(LegacyCleaningEventPort.class);
        deliveryEventPort = mock(LegacyDeliveryEventPort.class);
        dispatcher = new OneNetEventDispatcher(cleaningEventPort, deliveryEventPort,
                JsonMapper.builder().build());
    }

    @Test
    void cleanGross_wrappedInValue_routesToReportGross() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0001",
                "params":{"cleanGross":{"value":{"cleanOrderId":123,"weight":12.5,\
                "photoOpenOutside":"https://b/SN/clean/x/open_outside.jpg",\
                "photoCloseInside":"https://b/SN/clean/x/close_inside.jpg"},"time":1700000000000}}}}""";

        dispatcher.handle(json, "mq-msg-1");

        ArgumentCaptor<LegacyCleanGrossCommand> captor =
                ArgumentCaptor.forClass(LegacyCleanGrossCommand.class);
        verify(cleaningEventPort).acceptGross(captor.capture());
        LegacyCleanGrossCommand command = captor.getValue();
        assertThat(command.sn()).isEqualTo("EcoBin-SN-0001");
        assertThat(command.cleanOrderId()).isEqualTo(123L);
        assertThat(command.weight()).isEqualByComparingTo(new BigDecimal("12.5"));
        // 照片 URL（设备自定位置）随事件回传，分发器灌进 DTO
        assertThat(command.photoOpenOutside()).isEqualTo("https://b/SN/clean/x/open_outside.jpg");
        assertThat(command.photoCloseInside()).isEqualTo("https://b/SN/clean/x/close_inside.jpg");
    }

    @Test
    void cleanTare_unwrapped_routesToReportTare() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0002",
                "params":{"cleanTare":{"cleanOrderId":456,"weight":0.30}}}}""";

        dispatcher.handle(json, "mq-msg-1");

        ArgumentCaptor<LegacyCleanTareCommand> captor =
                ArgumentCaptor.forClass(LegacyCleanTareCommand.class);
        verify(cleaningEventPort).acceptTare(captor.capture());
        LegacyCleanTareCommand command = captor.getValue();
        assertThat(command.sn()).isEqualTo("EcoBin-SN-0002");
        assertThat(command.cleanOrderId()).isEqualTo(456L);
        assertThat(command.weight()).isEqualByComparingTo(new BigDecimal("0.30"));
    }

    @Test
    void deliveryComplete_routesToCompleteDelivery() {
        String json = """
                {"msgType":"thingEvent","subData":{"deviceName":"EcoBin-SN-0003",
                "params":{"deliveryComplete":{"value":{"doorIndex":2,"weight":3.2,\
                "photoOpenOutside":"https://b/a/open_outside.jpg","photoCloseInside":"https://b/a/close_inside.jpg"}}}}}""";

        dispatcher.handle(json, "mq-msg-1");

        ArgumentCaptor<LegacyDeliveryReportCommand> captor =
                ArgumentCaptor.forClass(LegacyDeliveryReportCommand.class);
        verify(deliveryEventPort).completeDelivery(captor.capture());
        LegacyDeliveryReportCommand command = captor.getValue();
        assertThat(command.sn()).isEqualTo("EcoBin-SN-0003");
        assertThat(command.messageId()).isEqualTo("mq-msg-1");
        assertThat(command.doorIndex()).isEqualTo(2);
        assertThat(command.weight()).isEqualByComparingTo(new BigDecimal("3.2"));
        // 照片 URL 随事件回传，分发器灌进 DTO
        assertThat(command.photoOpenOutside()).isEqualTo("https://b/a/open_outside.jpg");
        assertThat(command.photoCloseInside()).isEqualTo("https://b/a/close_inside.jpg");
    }

    @Test
    void unknownMsgType_isSkipped() {
        String json = """
                {"msgType":"thingProperty","subData":{"deviceName":"EcoBin-SN-0004","params":{"voltage":{"value":12.0}}}}""";

        dispatcher.handle(json, "mq-msg-1");

        verifyNoInteractions(cleaningEventPort, deliveryEventPort);
    }
}
