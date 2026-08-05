package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.persistence.BagTraceIdentityBatchRef;
import org.enveloping.ecobin.identity.api.result.BagTraceIdentityFacts;

public interface BagTraceIdentityQueryPort {

    BagTraceIdentityFacts resolve(BagTraceIdentityBatchRef batchRef);
}
