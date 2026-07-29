package org.enveloping.ecobin.device.application.delivery;

import org.enveloping.ecobin.device.api.persistence.DeliveryCompletionFactsRef;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionPersistenceFacts;

public interface DeliveryCompletionFactsRefFactory {

    DeliveryCompletionFactsRef issue(
            DeliveryCompletionPersistenceFacts facts);
}
