package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.DeliveryCompletionFactsRef;
import org.enveloping.ecobin.device.api.result.DeliveryCompletionBusinessResult;

@FunctionalInterface
public interface DeliveryCompletionBusinessWriter {

    DeliveryCompletionBusinessResult write(
            DeliveryCompletionFactsRef facts);
}
