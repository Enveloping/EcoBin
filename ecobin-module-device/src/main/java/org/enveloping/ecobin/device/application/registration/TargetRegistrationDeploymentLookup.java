package org.enveloping.ecobin.device.application.registration;

import org.enveloping.ecobin.identity.api.port.OrganizationUserRegistrationAttributionPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Service
public class TargetRegistrationDeploymentLookup
        implements OrganizationUserRegistrationAttributionPort {

    private final JdbcTemplate jdbc;
    private final RegistrationDeploymentRefFactory referenceFactory;

    public TargetRegistrationDeploymentLookup(
            JdbcTemplate jdbc,
            RegistrationDeploymentRefFactory referenceFactory) {
        this.jdbc = jdbc;
        this.referenceFactory = referenceFactory;
    }

    @Override
    @Transactional(propagation = Propagation.MANDATORY)
    public Optional<ResolvedRegistrationAttribution> resolve(
            RegistrationAttributionQuery query) {
        return jdbc.query("""
                        SELECT d.tenant_id, d.organization_id, d.id,
                               d.public_code
                        FROM dev_device_deployment d
                        JOIN dev_asset_active_deployment active
                          ON active.tenant_id = d.tenant_id
                         AND active.organization_id = d.organization_id
                         AND active.deployment_id = d.id
                        JOIN iam_tenant t ON t.id = d.tenant_id
                        JOIN iam_organization o
                          ON o.tenant_id = d.tenant_id
                         AND o.id = d.organization_id
                        WHERE d.public_code = ?
                          AND t.tenant_code = ?
                          AND o.organization_code = ?
                          AND d.lifecycle_status <> 'ENDED'
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ResolvedRegistrationAttribution(
                        rs.getString("public_code"),
                        referenceFactory.issue(
                                rs.getLong("tenant_id"),
                                rs.getLong("organization_id"),
                                rs.getLong("id"))),
                query.deploymentCode(),
                query.tenantCode(),
                query.organizationCode())
                .stream()
                .findFirst();
    }
}
