package org.enveloping.ecobin.recycling.api.legacy;

/**
 * OneNet/HTTP 上行适配器调用旧投递行为的公开入口。
 */
public interface LegacyDeliveryEventPort {

    void completeDelivery(LegacyDeliveryReportCommand command);
}
