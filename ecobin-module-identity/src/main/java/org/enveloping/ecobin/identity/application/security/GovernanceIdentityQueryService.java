package org.enveloping.ecobin.identity.application.security;

import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityBatchRef;
import org.enveloping.ecobin.identity.api.persistence.GovernanceIdentityFilterRef;
import org.enveloping.ecobin.identity.api.persistence.IdentityOwnedGovernanceFilterRefFactory;
import org.enveloping.ecobin.identity.api.port.GovernanceIdentityQueryPort;
import org.enveloping.ecobin.identity.api.result.GovernanceIdentityFacts;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
public class GovernanceIdentityQueryService
        implements GovernanceIdentityQueryPort {

    private final JdbcTemplate jdbc;
    private final IdentityOwnedGovernanceFilterRefFactory filters;

    public GovernanceIdentityQueryService(
            JdbcTemplate jdbc,
            IdentityOwnedGovernanceFilterRefFactory filters) {
        this.jdbc = jdbc;
        this.filters = filters;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY, readOnly = true)
    public GovernanceIdentityFilterRef prepareFilter(
            String organizationCode, UUID actorUid) {
        List<Long> organizations = organizationCode == null
                ? List.of() : jdbc.queryForList("""
                        SELECT id FROM iam_organization
                        WHERE organization_code = ?
                        """, Long.class, organizationCode);
        List<Long> platform = actorUid == null
                ? List.of() : jdbc.queryForList("""
                        SELECT id FROM iam_platform_admin
                        WHERE platform_admin_uid = ?
                        """, Long.class, actorUid.toString());
        List<Long> staff = actorUid == null
                ? List.of() : jdbc.queryForList("""
                        SELECT id FROM iam_staff_account
                        WHERE staff_account_uid = ?
                        """, Long.class, actorUid.toString());
        return filters.issue(
                organizationCode != null, organizations,
                actorUid != null, platform, staff);
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY, readOnly = true)
    public GovernanceIdentityFacts resolve(
            GovernanceIdentityBatchRef batch) {
        List<Relation> relations = new ArrayList<>();
        batch.consumeOnce((token, tenant, organization, actorKind,
                           platform, staff) -> relations.add(new Relation(
                token, tenant, organization, actorKind, platform, staff)));
        Map<Long, String> tenants = codes(
                relations.stream().map(Relation::tenantKey)
                        .filter(java.util.Objects::nonNull).distinct().toList(),
                "iam_tenant", "tenant_code");
        Map<Long, String> organizations = codes(
                relations.stream().map(Relation::organizationKey)
                        .filter(java.util.Objects::nonNull).distinct().toList(),
                "iam_organization", "organization_code");
        Map<Long, UUID> platform = uids(
                relations.stream().map(Relation::platformAdminKey)
                        .filter(java.util.Objects::nonNull).distinct().toList(),
                "iam_platform_admin", "platform_admin_uid");
        Map<Long, UUID> staff = uids(
                relations.stream().map(Relation::staffAccountKey)
                        .filter(java.util.Objects::nonNull).distinct().toList(),
                "iam_staff_account", "staff_account_uid");
        LinkedHashMap<UUID, GovernanceIdentityFacts.Entry> result =
                new LinkedHashMap<>();
        for (Relation relation : relations) {
            UUID actorUid = "PLATFORM_ADMIN".equals(relation.actorKind())
                    ? platform.get(relation.platformAdminKey())
                    : "STAFF_ACCOUNT".equals(relation.actorKind())
                    ? staff.get(relation.staffAccountKey()) : null;
            result.put(relation.token(), new GovernanceIdentityFacts.Entry(
                    tenants.get(relation.tenantKey()),
                    organizations.get(relation.organizationKey()),
                    actorUid));
        }
        return new GovernanceIdentityFacts(result);
    }

    private Map<Long, String> codes(
            List<Long> ids, String table, String column) {
        if (ids.isEmpty()) {
            return Map.of();
        }
        String placeholders = "?,".repeat(ids.size());
        placeholders = placeholders.substring(0, placeholders.length() - 1);
        LinkedHashMap<Long, String> result = new LinkedHashMap<>();
        jdbc.query("SELECT id, " + column + " value FROM " + table
                        + " WHERE id IN (" + placeholders + ")",
                (org.springframework.jdbc.core.RowCallbackHandler) rs ->
                        result.put(rs.getLong("id"), rs.getString("value")),
                ids.toArray());
        return result;
    }

    private Map<Long, UUID> uids(
            List<Long> ids, String table, String column) {
        if (ids.isEmpty()) {
            return Map.of();
        }
        String placeholders = "?,".repeat(ids.size());
        placeholders = placeholders.substring(0, placeholders.length() - 1);
        LinkedHashMap<Long, UUID> result = new LinkedHashMap<>();
        jdbc.query("SELECT id, " + column + " value FROM " + table
                        + " WHERE id IN (" + placeholders + ")",
                (org.springframework.jdbc.core.RowCallbackHandler) rs ->
                        result.put(rs.getLong("id"),
                                UUID.fromString(rs.getString("value"))),
                ids.toArray());
        return result;
    }

    private record Relation(
            UUID token,
            Long tenantKey,
            Long organizationKey,
            String actorKind,
            Long platformAdminKey,
            Long staffAccountKey) { }
}
