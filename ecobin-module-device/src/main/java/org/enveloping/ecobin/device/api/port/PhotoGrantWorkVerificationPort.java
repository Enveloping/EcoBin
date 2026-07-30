package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.result.PhotoGrantWorkVerification;
import org.enveloping.ecobin.device.api.result.TrustedPhotoGrantWork;

/**
 * Verifies the recycling work targeted by an authenticated photo grant
 * request without exposing recycling tables to the device module.
 */
@FunctionalInterface
public interface PhotoGrantWorkVerificationPort {

    PhotoGrantWorkVerification verify(TrustedPhotoGrantWork work);
}
