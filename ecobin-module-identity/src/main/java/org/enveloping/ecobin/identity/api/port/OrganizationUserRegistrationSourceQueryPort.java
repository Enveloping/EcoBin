package org.enveloping.ecobin.identity.api.port;

import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Consumer-shaped read port for the device-owned permanent asset details exposed
 * by organization-user directory projections.
 *
 * <p>Only public identity and asset identifiers cross the module
 * boundary. Identity never queries device-owned tables or receives a raw
 * asset primary key.</p>
 */
public interface OrganizationUserRegistrationSourceQueryPort {

    Map<UUID, RegistrationSourceSummary> findSources(
            RegistrationSourceQuery query);

    Set<UUID> findOrganizationUsers(
            RegistrationSourceUsersQuery query);

    record RegistrationSourceQuery(
            String tenantCode,
            String organizationCode,
            List<UUID> organizationUserUids) {

        public RegistrationSourceQuery {
            requireText(tenantCode, "tenantCode");
            requireText(organizationCode, "organizationCode");
            organizationUserUids = List.copyOf(organizationUserUids);
        }
    }

    record RegistrationSourceUsersQuery(
            String tenantCode,
            String organizationCode,
            String deviceCode) {

        public RegistrationSourceUsersQuery {
            requireText(tenantCode, "tenantCode");
            requireText(organizationCode, "organizationCode");
            requireText(deviceCode, "deviceCode");
        }
    }

    record RegistrationSourceSummary(
            String deviceCode,
            String lifecycleStatus) {

        public RegistrationSourceSummary {
            requireText(deviceCode, "deviceCode");
            requireText(lifecycleStatus, "lifecycleStatus");
        }
    }

    private static void requireText(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException(
                    name + " must not be blank");
        }
    }
}
