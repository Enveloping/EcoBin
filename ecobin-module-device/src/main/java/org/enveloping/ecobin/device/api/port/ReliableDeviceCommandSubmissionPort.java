package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.DeviceCommandSubmission;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;

/**
 * Transport Seam for submitting one frozen device command.
 *
 * <p>The caller owns leases and retries. An Adapter must never invent a new
 * command identity or report a device outcome from a platform HTTP response.
 */
public interface ReliableDeviceCommandSubmissionPort {

    DeviceCommandSubmissionResult submit(DeviceCommandSubmission submission);
}
