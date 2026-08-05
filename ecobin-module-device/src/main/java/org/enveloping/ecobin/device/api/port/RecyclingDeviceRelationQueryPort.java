package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.RecyclingDeviceRelationBatchRef;
import org.enveloping.ecobin.device.api.result.RecyclingDeviceRelationFacts;

public interface RecyclingDeviceRelationQueryPort {
    RecyclingDeviceRelationFacts resolve(
            RecyclingDeviceRelationBatchRef batch);
}
