package org.enveloping.ecobin.device.api.port;

import org.enveloping.ecobin.device.api.persistence.BagTraceCycleRef;
import org.enveloping.ecobin.device.api.persistence.BagTraceSessionSelectionRef;

public interface BagTraceSessionQueryPort {
    BagTraceSessionSelectionRef sessions(BagTraceCycleRef cycle);
}
