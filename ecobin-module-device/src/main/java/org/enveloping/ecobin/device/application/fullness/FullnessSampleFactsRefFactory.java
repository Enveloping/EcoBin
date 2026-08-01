package org.enveloping.ecobin.device.application.fullness;

import org.enveloping.ecobin.device.api.persistence.FullnessSamplePersistenceFactsRef;
import org.enveloping.ecobin.device.api.result.FullnessSamplePersistenceFacts;

public interface FullnessSampleFactsRefFactory {

    FullnessSamplePersistenceFactsRef issue(
            FullnessSamplePersistenceFacts facts);
}
