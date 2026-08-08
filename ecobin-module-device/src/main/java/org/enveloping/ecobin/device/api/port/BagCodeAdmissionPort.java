package org.enveloping.ecobin.device.api.port;

import java.util.Optional;

/**
 * Validates a physical bag label before a factory or cleaning installation.
 *
 * <p>The device module owns the factory-installed-bag consumer, while the
 * recycling module owns the label policy and supplies this port.  Keeping the
 * contract here avoids a reverse Maven dependency.</p>
 */
public interface BagCodeAdmissionPort {

    Optional<AuthenticatedBagCode> authenticate(String rawCode);

    record AuthenticatedBagCode(String value, String keyId) {
    }
}
