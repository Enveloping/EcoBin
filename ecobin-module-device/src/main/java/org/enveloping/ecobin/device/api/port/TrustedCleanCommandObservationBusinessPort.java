package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.TrustedCleanCommandObservation;

/** Projects trusted device-command evidence into its cleaning aggregate. */
public interface TrustedCleanCommandObservationBusinessPort {

    void applyCleanCommandObservation(
            TrustedCleanCommandObservation observation);
}
