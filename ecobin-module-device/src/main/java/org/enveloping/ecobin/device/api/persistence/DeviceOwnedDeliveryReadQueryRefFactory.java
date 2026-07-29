package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.application.deliveryquery.DeliveryReadQueryRefFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.List;
import java.util.Map;

@Component
final class DeviceOwnedDeliveryReadQueryRefFactory
        implements DeliveryReadQueryRefFactory {

    @Override
    public DeliveryOptionsBusinessQueryRef issueOptionsBusiness(
            long tenantKey,
            long organizationKey,
            long deploymentKey,
            List<DeliveryOptionsBusinessQueryRef.PortKey> ports) {
        DeliveryOptionsBusinessQueryRef reference =
                new DeliveryOptionsBusinessQueryRef(
                        tenantKey,
                        organizationKey,
                        deploymentKey,
                        ports,
                        issuingResources());
        return register(reference, reference::markTransactionCompleted);
    }

    @Override
    public DeliverySessionBusinessQueryRef issueSessionBusiness(
            long tenantKey,
            long organizationKey,
            long deliverySessionKey) {
        DeliverySessionBusinessQueryRef reference =
                new DeliverySessionBusinessQueryRef(
                        tenantKey,
                        organizationKey,
                        deliverySessionKey,
                        issuingResources());
        return register(reference, reference::markTransactionCompleted);
    }

    private static Map<Object, Object> issuingResources() {
        if (!TransactionSynchronizationManager.isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "delivery query reference requires an existing "
                            + "read-only transaction");
        }
        Map<Object, Object> resources =
                TransactionSynchronizationManager.getResourceMap();
        if (resources.isEmpty()) {
            throw new IllegalStateException(
                    "delivery query reference requires a bound "
                            + "transaction resource");
        }
        return resources;
    }

    private static <T> T register(
            T reference,
            Runnable completion) {
        TransactionSynchronizationManager.registerSynchronization(
                new TransactionSynchronization() {
                    @Override
                    public void afterCompletion(int status) {
                        completion.run();
                    }
                });
        return reference;
    }
}
