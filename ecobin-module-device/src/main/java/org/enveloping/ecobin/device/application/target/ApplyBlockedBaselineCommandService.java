package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.BlockedDeviceCommandBusinessPort;
import org.enveloping.ecobin.device.api.result.BlockedDeviceCommand;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/** Terminates baseline generations whose reliable command cannot converge. */
@Service
public class ApplyBlockedBaselineCommandService
        implements BlockedDeviceCommandBusinessPort {

    private final BaselineMeasurementTechnicalAbortService aborts;

    public ApplyBlockedBaselineCommandService(
            BaselineMeasurementTechnicalAbortService aborts) {
        this.aborts = aborts;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public void applyBlockedDeviceCommand(BlockedDeviceCommand blocked) {
        if (!"MEASURE_EMPTY_BAG_BASELINE".equals(blocked.commandType())) {
            return;
        }
        aborts.abortByCommand(
                blocked.commandUid(),
                blocked.reasonCode(),
                blocked.blockedAt());
    }
}
