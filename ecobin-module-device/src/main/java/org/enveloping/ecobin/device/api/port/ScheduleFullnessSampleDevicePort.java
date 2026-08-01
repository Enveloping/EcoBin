package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.command.ScheduleFullnessSampleCommand;
import org.enveloping.ecobin.device.api.result.ScheduledFullnessSample;

public interface ScheduleFullnessSampleDevicePort {

    ScheduledFullnessSample schedule(
            ScheduleFullnessSampleCommand command);
}
