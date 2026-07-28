package org.enveloping.ecobin.framework.reliability;

import java.util.Objects;

/**
 * Delayed, authority-owned scope resolution for a trusted inbound message.
 *
 * <p>The external Adapter cannot provide database keys. The owning Module
 * resolves them inside the reliable inbox receipt transaction and exposes
 * them only through this callback.</p>
 */
@FunctionalInterface
public interface TrustedInboxScopeResolver {

    void resolve(ScopeWriter writer);

    static TrustedInboxScopeResolver platform() {
        return writer -> Objects.requireNonNull(writer, "writer").platform();
    }

    @FunctionalInterface
    interface ScopeWriter {

        void write(String scopeKind, Long tenantKey, Long organizationKey);

        default void platform() {
            write("PLATFORM", null, null);
        }

        default void organization(long tenantKey, long organizationKey) {
            write("ORGANIZATION", tenantKey, organizationKey);
        }
    }
}
