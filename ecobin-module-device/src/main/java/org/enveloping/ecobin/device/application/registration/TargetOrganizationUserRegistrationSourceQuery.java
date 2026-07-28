package org.enveloping.ecobin.device.application.registration;

import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationSourceQueryPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
public class TargetOrganizationUserRegistrationSourceQuery
        implements OrganizationUserRegistrationSourceQueryPort {

    private final JdbcTemplate jdbc;

    public TargetOrganizationUserRegistrationSourceQuery(
            JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(
            propagation = Propagation.SUPPORTS,
            readOnly = true)
    public Map<UUID, RegistrationSourceSummary> findSources(
            RegistrationSourceQuery query) {
        if (query.organizationUserUids().isEmpty()) {
            return Map.of();
        }
        String placeholders = query.organizationUserUids().stream()
                .map(ignored -> "?")
                .collect(Collectors.joining(", "));
        List<Object> parameters = new ArrayList<>();
        parameters.add(query.tenantCode());
        parameters.add(query.organizationCode());
        query.organizationUserUids().forEach(
                uid -> parameters.add(uid.toString()));
        LinkedHashMap<UUID, RegistrationSourceSummary> result =
                new LinkedHashMap<>();
        List<SourceRow> rows = jdbc.query("""
                        SELECT u.organization_user_uid,
                               d.public_code,
                               d.lifecycle_status
                        FROM iam_organization_user u
                        JOIN iam_tenant t
                          ON t.id = u.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = u.tenant_id
                         AND o.id = u.organization_id
                        JOIN dev_device_deployment d
                          ON d.tenant_id = u.tenant_id
                         AND d.organization_id = u.organization_id
                         AND d.id = u.registered_via_deployment_id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                          AND u.organization_user_uid IN (%s)
                        """.formatted(placeholders),
                (rs, ignored) -> new SourceRow(
                        UUID.fromString(rs.getString(
                                "organization_user_uid")),
                        rs.getString("public_code"),
                        rs.getString("lifecycle_status")),
                parameters.toArray());
        rows.forEach(row -> result.put(
                row.organizationUserUid(),
                new RegistrationSourceSummary(
                        row.deploymentCode(),
                        row.lifecycleStatus())));
        return Map.copyOf(result);
    }

    @Override
    @Transactional(
            propagation = Propagation.SUPPORTS,
            readOnly = true)
    public Set<UUID> findOrganizationUsers(
            RegistrationSourceUsersQuery query) {
        return Set.copyOf(new LinkedHashSet<>(jdbc.query("""
                        SELECT u.organization_user_uid
                        FROM dev_device_deployment d
                        JOIN iam_tenant t
                          ON t.id = d.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = d.tenant_id
                         AND o.id = d.organization_id
                        JOIN iam_organization_user u
                          ON u.tenant_id = d.tenant_id
                         AND u.organization_id = d.organization_id
                         AND u.registered_via_deployment_id = d.id
                        WHERE t.tenant_code = ?
                          AND o.organization_code = ?
                          AND d.public_code = ?
                        """,
                (rs, ignored) -> UUID.fromString(
                        rs.getString("organization_user_uid")),
                query.tenantCode(),
                query.organizationCode(),
                query.deploymentCode())));
    }

    private record SourceRow(
            UUID organizationUserUid,
            String deploymentCode,
            String lifecycleStatus) {
    }
}
