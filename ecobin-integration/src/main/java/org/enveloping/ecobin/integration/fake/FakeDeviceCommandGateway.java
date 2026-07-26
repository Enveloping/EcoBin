package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.DeviceCommandGateway;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * 不建立任何网络连接或物理动作的 Fake OneNet 下行。
 */
public final class FakeDeviceCommandGateway implements DeviceCommandGateway {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(FakeDeviceCommandGateway.class);

    @Override
    public void openDeliveryDoor(String devSn, Integer doorIndex) {
        LOGGER.info(
                "[FAKE OneNet] blocked physical delivery-door action sn={} door={}",
                devSn,
                doorIndex);
    }

    @Override
    public void openCleanDoor(
            String devSn,
            Integer doorIndex,
            Long cleanOrderId) {
        LOGGER.info(
                "[FAKE OneNet] blocked physical cleaning-door action sn={} door={} operation={}",
                devSn,
                doorIndex,
                cleanOrderId);
    }
}
