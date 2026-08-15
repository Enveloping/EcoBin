package org.enveloping.ecobin.identity.application.platformminiapp;

import org.enveloping.ecobin.identity.api.persistence.FactoryOperatorPersistenceRef;

/** Identity-internal bridge for opaque factory-operator references. */
public interface FactoryOperatorPersistenceRefFactory {

    FactoryOperatorPersistenceRef issue(long factoryOperatorKey);
}
