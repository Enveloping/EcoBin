package org.enveloping.ecobin.identity.api.persistence;

/**
 * Relationship-specific, opaque persistence reference for the optional
 * device-deployment attribution of a newly created organization user.
 *
 * <p>The device-owned implementation is non-serializable and permits one
 * same-thread, same-transaction expansion. Identity can only use the keys to
 * write its own immutable registration foreign key.</p>
 */
public interface OrganizationUserRegistrationAttributionRef {

    void writeForeignKeyTo(ForeignKeyWriter writer);

    @FunctionalInterface
    interface ForeignKeyWriter {
        void write(
                long tenantKey,
                long organizationKey,
                long deploymentKey);
    }
}
