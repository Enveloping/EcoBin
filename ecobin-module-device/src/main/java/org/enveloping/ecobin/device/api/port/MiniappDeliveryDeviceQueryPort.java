package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.query.DeliveryDeviceOptionsQuery;
import org.enveloping.ecobin.device.api.query.OwnedDeliverySessionQuery;
import org.enveloping.ecobin.device.api.result.DeliveryDeviceOptionsSnapshot;
import org.enveloping.ecobin.device.api.result.OwnedDeliverySessionSnapshot;

/**
 * 普通用户小程序投递 GET 使用的 device 只读查询端口。
 */
public interface MiniappDeliveryDeviceQueryPort {

    DeliveryDeviceOptionsSnapshot deliveryOptions(
            DeliveryDeviceOptionsQuery query);

    OwnedDeliverySessionSnapshot ownedSession(
            OwnedDeliverySessionQuery query);
}
