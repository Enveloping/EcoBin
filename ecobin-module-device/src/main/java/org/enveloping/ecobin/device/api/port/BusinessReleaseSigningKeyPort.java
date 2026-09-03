package org.enveloping.ecobin.device.api.port;

/** Read-only access to offline-release public keys.  Private keys never enter the backend. */
public interface BusinessReleaseSigningKeyPort {

    Readiness readiness();

    byte[] loadEd25519PublicKey(String signingKeyId);

    record Readiness(boolean available, String message) {
    }
}
