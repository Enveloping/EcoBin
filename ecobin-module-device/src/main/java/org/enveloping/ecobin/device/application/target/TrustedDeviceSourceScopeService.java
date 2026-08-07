package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class TrustedDeviceSourceScopeService
        implements TrustedDeviceSourceScopePort {

    private final JdbcTemplate jdbc;

    public TrustedDeviceSourceScopeService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public TrustedInboxScopeResolver resolverForPlatformAsset(String hardwareSn) {
        String trustedHardwareSn = requireHardwareSn(hardwareSn);
        return writer -> {
            Integer count = jdbc.queryForObject("""
                            SELECT COUNT(*)
                            FROM dev_device_asset
                            WHERE hardware_sn = ?
                            """,
                    Integer.class,
                    trustedHardwareSn);
            if (count == null || count != 1) {
                throw new UntrustedInboxSourceException(
                        "authenticated device asset is not registered");
            }
            writer.platform();
        };
    }

    @Override
    public TrustedInboxScopeResolver resolverForOrganizationAsset(
            String hardwareSn) {
        String trustedHardwareSn = requireHardwareSn(hardwareSn);
        return writer -> {
            List<AssetScope> rows = assetScopes(trustedHardwareSn);
            if (rows.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "authenticated device asset is not registered");
            }
            AssetScope scope = rows.getFirst();
            if (scope.tenantId() == null || scope.organizationId() == null) {
                throw new UntrustedInboxSourceException(
                        "device asset is not yet assigned to an organization");
            }
            writer.organization(scope.tenantId(), scope.organizationId());
        };
    }

    private List<AssetScope> assetScopes(String hardwareSn) {
        return jdbc.query("""
                        SELECT tenant_id, organization_id
                        FROM dev_device_asset
                        WHERE hardware_sn = ?
                        """,
                (rs, ignored) -> new AssetScope(
                        (Long) rs.getObject("tenant_id"),
                        (Long) rs.getObject("organization_id")),
                hardwareSn);
    }

    private static String requireHardwareSn(String value) {
        if (value == null || value.isBlank() || value.length() > 64
                || !value.equals(value.trim())) {
            throw new UntrustedInboxSourceException(
                    "authenticated device name is invalid");
        }
        return value;
    }

    private record AssetScope(Long tenantId, Long organizationId) {
    }
}
