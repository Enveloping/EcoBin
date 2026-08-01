package org.enveloping.ecobin.identity.application.deliveryorder;

import org.enveloping.ecobin.identity.api.id.OrganizationUserUid;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Repository;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

@Repository
class JdbcDeliveryOrderIdentityRepository
        implements DeliveryOrderIdentityRepository {

    private final NamedParameterJdbcTemplate jdbc;

    JdbcDeliveryOrderIdentityRepository(
            NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Map<Long, OrganizationScopeRow> findOrganizationScopes(
            Set<Long> organizationIds) {
        if (organizationIds.isEmpty()) {
            return Map.of();
        }
        List<OrganizationScopeRow> rows = jdbc.query("""
                        SELECT tenant_id, id AS organization_id
                        FROM iam_organization
                        WHERE id IN (:organizationIds)
                        """,
                new MapSqlParameterSource(
                        "organizationIds",
                        organizationIds),
                (rs, ignored) -> new OrganizationScopeRow(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id")));
        LinkedHashMap<Long, OrganizationScopeRow> result =
                new LinkedHashMap<>();
        rows.forEach(row ->
                result.put(row.organizationId(), row));
        return Map.copyOf(result);
    }

    @Override
    public Map<Long, OrganizationUserRow> findOrganizationUsers(
            Set<Long> organizationUserIds) {
        if (organizationUserIds.isEmpty()) {
            return Map.of();
        }
        List<OrganizationUserRow> rows = jdbc.query("""
                        SELECT tenant_id,
                               organization_id,
                               id AS organization_user_id,
                               organization_user_uid
                        FROM iam_organization_user
                        WHERE id IN (:organizationUserIds)
                        """,
                new MapSqlParameterSource(
                        "organizationUserIds",
                        organizationUserIds),
                (rs, ignored) -> new OrganizationUserRow(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("organization_user_id"),
                        UUID.fromString(
                                rs.getString(
                                        "organization_user_uid"))));
        LinkedHashMap<Long, OrganizationUserRow> result =
                new LinkedHashMap<>();
        rows.forEach(row ->
                result.put(row.organizationUserId(), row));
        return Map.copyOf(result);
    }

    @Override
    public Map<Long, PlatformAdminRow> findPlatformAdministrators(
            Set<Long> platformAdminIds) {
        if (platformAdminIds.isEmpty()) {
            return Map.of();
        }
        List<PlatformAdminRow> rows = jdbc.query("""
                        SELECT id AS platform_admin_id,
                               platform_admin_uid,
                               display_name
                        FROM iam_platform_admin
                        WHERE id IN (:platformAdminIds)
                        """,
                new MapSqlParameterSource(
                        "platformAdminIds",
                        platformAdminIds),
                (rs, ignored) -> new PlatformAdminRow(
                        rs.getLong("platform_admin_id"),
                        UUID.fromString(
                                rs.getString("platform_admin_uid")),
                        rs.getString("display_name")));
        LinkedHashMap<Long, PlatformAdminRow> result =
                new LinkedHashMap<>();
        rows.forEach(row ->
                result.put(row.platformAdminId(), row));
        return Map.copyOf(result);
    }

    @Override
    public Map<Long, StaffAccountRow> findStaffAccounts(
            Set<Long> staffAccountIds) {
        if (staffAccountIds.isEmpty()) {
            return Map.of();
        }
        List<StaffAccountRow> rows = jdbc.query("""
                        SELECT tenant_id,
                               id AS staff_account_id,
                               staff_account_uid,
                               account_kind,
                               display_name
                        FROM iam_staff_account
                        WHERE id IN (:staffAccountIds)
                        """,
                new MapSqlParameterSource(
                        "staffAccountIds",
                        staffAccountIds),
                (rs, ignored) -> new StaffAccountRow(
                        rs.getLong("tenant_id"),
                        rs.getLong("staff_account_id"),
                        UUID.fromString(
                                rs.getString("staff_account_uid")),
                        rs.getString("account_kind"),
                        rs.getString("display_name")));
        LinkedHashMap<Long, StaffAccountRow> result =
                new LinkedHashMap<>();
        rows.forEach(row ->
                result.put(row.staffAccountId(), row));
        return Map.copyOf(result);
    }

    @Override
    public Optional<OrganizationUserFilterRow> findOrganizationUserFilter(
            String tenantCode,
            String organizationCode,
            OrganizationUserUid organizationUserUid) {
        return jdbc.query("""
                        SELECT t.id AS tenant_id,
                               o.id AS organization_id,
                               u.id AS organization_user_id,
                               t.tenant_code,
                               o.organization_code,
                               u.organization_user_uid
                        FROM iam_tenant t
                        JOIN iam_organization o
                          ON o.tenant_id = t.id
                        JOIN iam_organization_user u
                          ON u.tenant_id = t.id
                         AND u.organization_id = o.id
                        WHERE t.tenant_code = :tenantCode
                          AND o.organization_code = :organizationCode
                          AND u.organization_user_uid =
                              :organizationUserUid
                        """,
                new MapSqlParameterSource()
                        .addValue("tenantCode", tenantCode)
                        .addValue(
                                "organizationCode",
                                organizationCode)
                        .addValue(
                                "organizationUserUid",
                                organizationUserUid.value().toString()),
                (rs, ignored) -> new OrganizationUserFilterRow(
                        rs.getLong("tenant_id"),
                        rs.getLong("organization_id"),
                        rs.getLong("organization_user_id"),
                        rs.getString("tenant_code"),
                        rs.getString("organization_code"),
                        UUID.fromString(
                                rs.getString(
                                        "organization_user_uid"))))
                .stream()
                .findFirst();
    }
}
