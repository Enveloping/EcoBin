package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.identity.api.port.IdentityOperationalOverviewQueryPort;
import org.enveloping.ecobin.identity.api.result.IdentityOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.IdentityOwnedRegistrationAssetAttributionRefFactory;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;

@Service
public class IdentityOperationalOverviewQueryService
        implements IdentityOperationalOverviewQueryPort {

    private final JdbcTemplate jdbc;
    private final IdentityOwnedRegistrationAssetAttributionRefFactory refs;

    public IdentityOperationalOverviewQueryService(
            JdbcTemplate jdbc,
            IdentityOwnedRegistrationAssetAttributionRefFactory refs) {
        this.jdbc = jdbc;
        this.refs = refs;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public IdentityOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive) {
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .IDENTITY_OPERATIONAL_OVERVIEW,
                (tenantId, organizations, platformId, staffId) -> queryOwned(
                        tenantId, organizations, from, toExclusive));
    }

    private IdentityOperationalOverview queryOwned(
            Long tenantId,
            List<ManagementScopePersistenceRef.OrganizationKey> organizations,
            Instant from,
            Instant toExclusive) {
        if (tenantId == null || organizations.isEmpty()) {
            return new IdentityOperationalOverview(List.of());
        }
        List<Long> organizationIds = organizations.stream()
                .map(ManagementScopePersistenceRef.OrganizationKey::value)
                .toList();
        String placeholders = "?,".repeat(organizationIds.size());
        placeholders = placeholders.substring(0, placeholders.length() - 1);
        List<Object> args = new ArrayList<>();
        args.add(time(from));
        args.add(time(toExclusive));
        args.add(tenantId);
        args.addAll(organizationIds);
        List<OrgRow> rows = jdbc.query("""
                        SELECT organization.id,
                               organization.organization_code,
                               organization.organization_name,
                               COUNT(user.id) registered_count,
                               SUM(CASE WHEN user.id IS NOT NULL
                                        AND user.registered_via_asset_id
                                            IS NULL
                                   THEN 1 ELSE 0 END) direct_count
                        FROM iam_organization organization
                        LEFT JOIN iam_organization_user user
                          ON user.tenant_id = organization.tenant_id
                         AND user.organization_id = organization.id
                         AND user.registered_at >= ?
                         AND user.registered_at < ?
                        WHERE organization.tenant_id = ?
                          AND organization.id IN (
                        """ + placeholders + ")" + """
                        GROUP BY organization.id,
                                 organization.organization_code,
                                 organization.organization_name
                        ORDER BY organization.organization_code
                        """,
                (rs, ignored) -> new OrgRow(
                        rs.getLong("id"),
                        rs.getString("organization_code"),
                        rs.getString("organization_name"),
                        rs.getLong("registered_count"),
                        rs.getLong("direct_count")),
                args.toArray());
        List<IdentityOperationalOverview.Organization> result =
                rows.stream().map(row -> new IdentityOperationalOverview.Organization(
                        row.code(), row.name(), row.registered(),
                        row.direct(), attribution(row.id(), from, toExclusive)))
                        .toList();
        return new IdentityOperationalOverview(result);
    }

    private List<org.enveloping.ecobin.identity.api.persistence
            .RegistrationAssetAttributionRef> attribution(
            long organizationId, Instant from, Instant to) {
        return jdbc.query("""
                        SELECT user.registered_via_asset_id asset_id,
                               COUNT(*) registered_count
                        FROM iam_organization_user user
                        WHERE user.organization_id = ?
                          AND user.registered_via_asset_id IS NOT NULL
                          AND user.registered_at >= ?
                          AND user.registered_at < ?
                        GROUP BY user.registered_via_asset_id
                        ORDER BY user.registered_via_asset_id
                        """,
                (rs, ignored) -> refs.issue(
                        rs.getLong("asset_id"),
                        rs.getLong("registered_count")),
                organizationId, time(from), time(to));
    }

    private static LocalDateTime time(Instant value) {
        return LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }

    private record OrgRow(
            long id, String code, String name, long registered, long direct) { }
}
