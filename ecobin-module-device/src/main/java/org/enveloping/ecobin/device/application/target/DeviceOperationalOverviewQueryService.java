package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceOperationalOverviewQueryPort;
import org.enveloping.ecobin.device.api.result.DeviceOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.identity.api.result.IdentityOperationalOverview;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;

@Service
public class DeviceOperationalOverviewQueryService
        implements DeviceOperationalOverviewQueryPort {

    private final JdbcTemplate jdbc;

    public DeviceOperationalOverviewQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public DeviceOperationalOverview query(
            ManagementScopePersistenceRef scope,
            List<IdentityOperationalOverview.Organization> organizations) {
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .DEVICE_OPERATIONAL_OVERVIEW,
                (tenantId, scopedOrganizations, platformId, staffId) ->
                        queryOwned(tenantId, scopedOrganizations,
                                organizations));
    }

    private DeviceOperationalOverview queryOwned(
            Long tenantId,
            List<ManagementScopePersistenceRef.OrganizationKey> scoped,
            List<IdentityOperationalOverview.Organization> identities) {
        if (tenantId == null || scoped.isEmpty()) {
            return new DeviceOperationalOverview(
                    java.util.Map.of(), java.util.Map.of());
        }
        List<Long> organizationIds = scoped.stream()
                .map(ManagementScopePersistenceRef.OrganizationKey::value)
                .toList();
        java.util.Map<Long, String> organizationCodes = scoped.stream()
                .collect(java.util.stream.Collectors.toMap(
                        ManagementScopePersistenceRef.OrganizationKey::value,
                        ManagementScopePersistenceRef.OrganizationKey::code));
        String placeholders = placeholders(organizationIds.size());
        List<Object> args = new ArrayList<>();
        args.add(tenantId);
        args.addAll(organizationIds);
        LinkedHashMap<String, Long> online = new LinkedHashMap<>();
        LinkedHashMap<Long, AssetRow> assets =
                new LinkedHashMap<>();
        jdbc.query("""
                        SELECT asset.id, asset.organization_id,
                               asset.device_public_code,
                               COALESCE(configuration.device_display_name,
                                        asset.device_public_code) display_name,
                               asset.lifecycle_status,
                               runtime.edge_connection_status
                        FROM dev_device_asset asset
                        LEFT JOIN dev_device_runtime_state runtime
                          ON runtime.asset_id = asset.id
                        LEFT JOIN dev_config_version configuration
                          ON configuration.id = (
                            SELECT latest.id FROM dev_config_version latest
                            WHERE latest.asset_id = asset.id
                            ORDER BY latest.version_no DESC LIMIT 1
                          )
                        WHERE asset.tenant_id = ?
                          AND asset.organization_id IN (
                        """ + placeholders + ")" + """
                        ORDER BY asset.organization_id, asset.id
                        """,
                (org.springframework.jdbc.core.RowCallbackHandler) rs -> {
                    long assetId = rs.getLong("id");
                    long organizationId = rs.getLong("organization_id");
                    assets.put(assetId,
                            new AssetRow(
                                    organizationId,
                                    rs.getString("device_public_code"),
                                    rs.getString("display_name")));
                    if ("NORMAL".equals(rs.getString("lifecycle_status"))
                            && "ONLINE".equals(rs.getString(
                            "edge_connection_status"))) {
                        online.merge(organizationCodes.get(organizationId),
                                1L, Long::sum);
                    }
                },
                args.toArray());
        LinkedHashMap<String, List<DeviceOperationalOverview
                .RegistrationAttribution>> attributions = new LinkedHashMap<>();
        for (IdentityOperationalOverview.Organization organization : identities) {
            java.util.ArrayList<DeviceOperationalOverview
                    .RegistrationAttribution> values = new java.util.ArrayList<>();
            for (var reference : organization.byAsset()) {
                values.add(reference.resolveOnce((assetId, count) -> {
                    AssetRow asset = assets.get(assetId);
                    if (asset == null
                            || !organization.organizationCode().equals(
                            organizationCodes.get(asset.organizationId()))) {
                        throw new IllegalStateException(
                                "registration asset is outside its authorized organization");
                    }
                    return new DeviceOperationalOverview.RegistrationAttribution(
                            asset.code(), asset.name(), count);
                }));
            }
            attributions.put(organization.organizationCode(), values);
        }
        return new DeviceOperationalOverview(online, attributions);
    }

    private static String placeholders(int size) {
        String value = "?,".repeat(size);
        return value.substring(0, value.length() - 1);
    }

    private record AssetRow(
            long organizationId, String code, String name) { }
}
