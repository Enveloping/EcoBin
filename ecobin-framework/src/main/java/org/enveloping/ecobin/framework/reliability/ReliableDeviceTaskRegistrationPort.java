package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

/**
 * Registers a stable device intent in the caller's existing transaction.
 */
public interface ReliableDeviceTaskRegistrationPort {

    UUID register(ReliableDeviceTaskRegistration registration);
}
