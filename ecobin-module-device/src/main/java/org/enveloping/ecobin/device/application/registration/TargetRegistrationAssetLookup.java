package org.enveloping.ecobin.device.application.registration;

import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Service
public class TargetRegistrationAssetLookup
        implements OrganizationUserRegistrationAttributionPort {

    private final JdbcTemplate jdbc;
    private final RegistrationAssetRefFactory referenceFactory;

    public TargetRegistrationAssetLookup(
            JdbcTemplate jdbc,
            RegistrationAssetRefFactory referenceFactory) {
        this.jdbc = jdbc;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public Optional<ResolvedRegistrationAttribution> resolve(
            RegistrationAttributionQuery query) {
        return jdbc.query("""
                        SELECT a.tenant_id, a.organization_id, a.id,
                               a.device_public_code
                        FROM dev_device_asset a
                        JOIN iam_tenant t ON t.id = a.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = a.tenant_id
                         AND o.id = a.organization_id
                        WHERE a.device_public_code = ?
                          AND t.tenant_code = ?
                          AND o.organization_code = ?
                          AND a.lifecycle_status = 'NORMAL'
                          AND a.acceptance_status = 'PASSED'
                          AND a.miniapp_qr_status = 'READY'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ResolvedRegistrationAttribution(
                        rs.getString("device_public_code"),
                        referenceFactory.issue(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id"),
                                rs.getLong("id"))),
                query.deviceCode(),
                query.tenantCode(),
                query.organizationCode())
                .stream()
                .findFirst();
    }
}
