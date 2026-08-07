package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.persistence.DeviceOwnedRecyclingPortRefFactory;
import org.enveloping.ecobin.device.api.port.RecyclingDevicePortLookupPort;
import org.enveloping.ecobin.device.api.result.ResolvedRecyclingDevicePort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RecyclingDevicePortLookupService
        implements RecyclingDevicePortLookupPort {

    private final JdbcTemplate jdbc;
    private final DeviceOwnedRecyclingPortRefFactory refs;

    public RecyclingDevicePortLookupService(
            JdbcTemplate jdbc,
            DeviceOwnedRecyclingPortRefFactory refs) {
        this.jdbc = jdbc;
        this.refs = refs;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public ResolvedRecyclingDevicePort requirePort(
            ManagementScopePersistenceRef scope,
            String deviceCode,
            int portNo) {
        if (deviceCode == null || deviceCode.isBlank()
                || portNo < 1 || portNo > 6) {
            throw notFound();
        }
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .RECYCLING_FULLNESS_QUERY,
                (tenantId, organizations, platformId, staffId) -> {
                    if (tenantId == null || organizations.size() != 1) {
                        throw new IllegalStateException(
                                "fullness port scope must contain one organization");
                    }
                    long organizationId = organizations.getFirst().value();
                    return jdbc.query("""
                                    SELECT port.id, asset.device_public_code,
                                           port.port_no
                                    FROM dev_port port
                                    JOIN dev_device_asset asset
                                      ON asset.id = port.asset_id
                                     AND asset.tenant_id = port.tenant_id
                                     AND asset.organization_id = port.organization_id
                                    WHERE port.tenant_id = ?
                                      AND port.organization_id = ?
                                      AND asset.device_public_code = ?
                                      AND asset.lifecycle_status = 'NORMAL'
                                      AND port.port_no = ?
                                    """,
                            (rs, ignored) -> new ResolvedRecyclingDevicePort(
                                    rs.getString("device_public_code"),
                                    rs.getInt("port_no"),
                                    refs.issue(tenantId, organizationId,
                                            rs.getLong("id"))),
                            tenantId, organizationId,
                            deviceCode.trim(), portNo).stream()
                            .findFirst().orElseThrow(
                                    RecyclingDevicePortLookupService::notFound);
                });
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404, "RESOURCE.NOT_FOUND", "资源不存在");
    }
}
