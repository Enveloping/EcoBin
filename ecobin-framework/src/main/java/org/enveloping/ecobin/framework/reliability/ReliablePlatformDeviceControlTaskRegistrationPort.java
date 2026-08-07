package org.enveloping.ecobin.framework.reliability;

import java.util.UUID;

public interface ReliablePlatformDeviceControlTaskRegistrationPort {

    UUID register(ReliablePlatformDeviceControlTaskRegistration registration);
}
