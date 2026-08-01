package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceInboxEventPort;
import org.enveloping.ecobin.device.api.result.TrustedDeviceEventApplyResult;
import org.enveloping.ecobin.device.api.result.TrustedDeviceInboxEvent;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

/**
 * Explicit routing boundary for the small set of registered OneNet event
 * contracts. Unknown/legacy kinds never reach a business projection.
 */
@Service
public class TrustedDeviceEventRouter
        implements TrustedDeviceInboxEventPort {

    private final TrustedConfigurationProgressService configurationProgress;
    private final TrustedOrangePiRuntimeFactService runtimeFacts;

    public TrustedDeviceEventRouter(
            TrustedConfigurationProgressService configurationProgress,
            TrustedOrangePiRuntimeFactService runtimeFacts) {
        this.configurationProgress = configurationProgress;
        this.runtimeFacts = runtimeFacts;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public TrustedDeviceEventApplyResult apply(
            TrustedDeviceInboxEvent inboxEvent) {
        if ("CONFIGURATION_PROGRESS".equals(
                inboxEvent.messageKind())) {
            return configurationProgress.apply(inboxEvent);
        }
        return runtimeFacts.apply(inboxEvent);
    }
}
