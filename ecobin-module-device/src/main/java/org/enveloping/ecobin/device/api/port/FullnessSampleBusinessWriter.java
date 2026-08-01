package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.FullnessSamplePersistenceFactsRef;
import org.enveloping.ecobin.device.api.result.FullnessSampleBusinessResult;

@FunctionalInterface
public interface FullnessSampleBusinessWriter {

    FullnessSampleBusinessResult write(
            FullnessSamplePersistenceFactsRef facts);
}
