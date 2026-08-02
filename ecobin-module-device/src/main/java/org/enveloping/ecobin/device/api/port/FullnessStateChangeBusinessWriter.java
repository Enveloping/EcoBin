package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.FullnessStateChangePersistenceFactsRef;
import org.enveloping.ecobin.device.api.result.FullnessStateChangeBusinessResult;

@FunctionalInterface
public interface FullnessStateChangeBusinessWriter {

    FullnessStateChangeBusinessResult write(
            FullnessStateChangePersistenceFactsRef facts);
}
