package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.persistence.DeliveryOrderIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.DeliveryOrganizationUserFilterRef;
import org.enveloping.ecobin.identity.api.query.DeliveryOrganizationUserFilterQuery;
import org.enveloping.ecobin.identity.api.result.DeliveryOrderIdentityFacts;

import java.util.Optional;

public interface DeliveryOrderIdentityQueryPort {

    DeliveryOrderIdentityFacts resolveFacts(
            DeliveryOrderIdentityBatchRef batchRef);

    Optional<DeliveryOrganizationUserFilterRef>
    resolveOrganizationUserFilter(
            DeliveryOrganizationUserFilterQuery query);
}
