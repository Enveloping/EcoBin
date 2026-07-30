package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.PhotoStatusBusinessResult;
import org.enveloping.ecobin.device.api.result.TrustedPhotoStatusFact;

/**
 * Applies one device-authenticated photo terminal fact to the recycling
 * projection in the same transaction as the device event.
 */
@FunctionalInterface
public interface ApplyTrustedPhotoStatusBusinessPort {

    PhotoStatusBusinessResult apply(TrustedPhotoStatusFact fact);
}
