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
    public TrustedInboxScopeResolver resolverForAsset(String hardwareSn) {
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
    public TrustedInboxScopeResolver resolverFor(
            String hardwareSn,
            String deploymentCode) {
        String trustedHardwareSn = requireHardwareSn(hardwareSn);
        String trustedDeploymentCode =
                requireDeploymentCode(deploymentCode);
        return writer -> {
            List<OrganizationScope> rows = jdbc.query("""
                            SELECT
                                deployment.tenant_id,
                                deployment.organization_id
                            FROM dev_device_asset asset
                            JOIN dev_device_deployment deployment
                              ON deployment.asset_id = asset.id
                            WHERE asset.hardware_sn = ?
                              AND deployment.public_code = ?
                            """,
                    (rs, ignored) -> new OrganizationScope(
                            rs.getLong("tenant_id"),
                            rs.getLong("organization_id")),
                    trustedHardwareSn,
                    trustedDeploymentCode);
            if (rows.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "authenticated device does not own the deployment");
            }
            OrganizationScope scope = rows.getFirst();
            writer.organization(
                    scope.tenantId(), scope.organizationId());
        };
    }

    private static String requireHardwareSn(String value) {
        if (value == null || value.isBlank() || value.length() > 64
                || !value.equals(value.trim())) {
            throw new UntrustedInboxSourceException(
                    "authenticated device name is invalid");
        }
        return value;
    }

    private static String requireDeploymentCode(String value) {
        if (value == null
                || !value.matches("^Dp_[A-Za-z0-9_-]{6,61}$")) {
            throw new UntrustedInboxSourceException(
                    "deployment code is invalid");
        }
        return value;
    }

    private record OrganizationScope(
            long tenantId,
            long organizationId) {
    }
}
