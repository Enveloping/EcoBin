package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.DeviceCommandGateway;
import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * 不建立任何网络连接或物理动作的 Fake OneNet 下行。
 */
public final class FakeDeviceCommandGateway
        implements DeviceCommandGateway, ReliableDeviceCommandSubmissionPort {

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

    @Override
    public DeviceCommandSubmissionResult submit(
            DeviceCommandSubmission submission) {
        LOGGER.info(
                "[FAKE OneNet] blocked reliable command type={} task={}",
                submission.commandType(),
                submission.taskUid());
        return new DeviceCommandSubmissionResult(
                DeviceCommandSubmissionResult.Outcome.RETRYABLE_FAILURE,
                null,
                null,
                null,
                "FAKE_CHANNEL_BLOCKED",
                "fake external mode blocks OneNet submission");
    }
}
