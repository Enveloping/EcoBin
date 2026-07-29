package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

/**
 * Registers a stable protocol-control intent in the caller's transaction.
 */
public interface ReliableDeviceControlTaskRegistrationPort {

    UUID register(ReliableDeviceControlTaskRegistration registration);
}
