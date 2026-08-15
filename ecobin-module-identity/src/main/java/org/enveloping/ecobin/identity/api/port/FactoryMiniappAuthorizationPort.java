package org.enveloping.ecobin.identity.api.port;

import org.enveloping.ecobin.identity.api.result.AuthorizedFactoryOperatorIdentity;

/** Request-local authorization boundary for factory mini-program endpoints. */
public interface FactoryMiniappAuthorizationPort {

    String ACCEPTANCE_READ = "factory.acceptance.read";
    String BAG_INSTALL = "factory.bag.install";
    String BAG_CORRECT = "factory.bag.correct";

    AuthorizedFactoryOperatorIdentity requireCapability(
            String requiredCapability);
}
