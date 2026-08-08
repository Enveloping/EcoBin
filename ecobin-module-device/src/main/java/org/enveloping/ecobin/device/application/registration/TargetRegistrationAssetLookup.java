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
                               a.device_public_code, t.tenant_code,
                               o.organization_code, o.organization_name
                        FROM dev_device_asset a
                        JOIN iam_tenant t ON t.id = a.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = a.tenant_id
                         AND o.id = a.organization_id
                        JOIN iam_organization_miniapp_binding b
                          ON b.tenant_id = a.tenant_id
                         AND b.organization_id = a.organization_id
                         AND b.status = 'ACTIVE'
                        JOIN iam_miniapp_channel c
                          ON c.id = b.miniapp_channel_id
                        WHERE a.device_public_code = ?
                          AND c.appid = ?
                          AND t.status = 'ENABLED'
                          AND o.status = 'ENABLED'
                          AND a.lifecycle_status = 'NORMAL'
                          AND a.acceptance_status = 'PASSED'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ResolvedRegistrationAttribution(
                        rs.getString("device_public_code"),
                        rs.getString("tenant_code"),
                        rs.getString("organization_code"),
                        rs.getString("organization_name"),
                        referenceFactory.issue(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id"),
                                rs.getLong("id"))),
                query.deviceCode(),
                query.channelAppId())
                .stream()
                .findFirst();
    }
}
