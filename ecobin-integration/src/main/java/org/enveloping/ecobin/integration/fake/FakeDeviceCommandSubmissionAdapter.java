package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.ReliableDeviceCommandSubmissionPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Network-free replacement for reliable OneNet command submission.
 */
public final class FakeDeviceCommandSubmissionAdapter
        implements ReliableDeviceCommandSubmissionPort {

    private static final Logger LOGGER =
            LoggerFactory.getLogger(
                    FakeDeviceCommandSubmissionAdapter.class);

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
