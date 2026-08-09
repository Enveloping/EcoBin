package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.TrustedDeviceSourceScopePort;
import org.enveloping.ecobin.framework.reliability.TrustedInboxScopeResolver;
import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
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
    public TrustedInboxScopeResolver resolverForBusinessConfirmation(
            String hardwareSn,
            String confirmationUid) {
        String trustedHardwareSn = requireHardwareSn(hardwareSn);
        String trustedConfirmationUid = requireConfirmationUid(
                confirmationUid);
        return writer -> {
            List<ConfirmationScope> rows = jdbc.query("""
                            SELECT task.scope_kind,
                                   task.tenant_id,
                                   task.organization_id
                            FROM ops_reliable_task task
                            JOIN dev_device_asset asset
                              ON asset.id = task.source_device_asset_id
                            WHERE asset.hardware_sn = ?
                              AND task.task_type = 'CONFIRM_EDGE_EVENT'
                              AND task.target_type =
                                  'BUSINESS_CONFIRMATION'
                              AND task.target_stable_key = ?
                              AND task.source_device_command_id IS NULL
                              AND (
                                  (
                                      task.scope_kind = 'PLATFORM'
                                      AND task.tenant_id IS NULL
                                      AND task.organization_id IS NULL
                                  )
                                  OR
                                  (
                                      task.scope_kind = 'ORGANIZATION'
                                      AND asset.tenant_id = task.tenant_id
                                      AND asset.organization_id =
                                          task.organization_id
                                  )
                              )
                            """,
                    (rs, ignored) -> new ConfirmationScope(
                            rs.getString("scope_kind"),
                            (Long) rs.getObject("tenant_id"),
                            (Long) rs.getObject("organization_id")),
                    trustedHardwareSn,
                    trustedConfirmationUid);
            if (rows.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "business confirmation does not belong to the "
                                + "authenticated device");
            }
            ConfirmationScope scope = rows.getFirst();
            if ("PLATFORM".equals(scope.scopeKind())
                    && scope.tenantId() == null
                    && scope.organizationId() == null) {
                writer.platform();
                return;
            }
            if ("ORGANIZATION".equals(scope.scopeKind())
                    && scope.tenantId() != null
                    && scope.organizationId() != null) {
                writer.organization(
                        scope.tenantId(), scope.organizationId());
                return;
            }
            throw new UntrustedInboxSourceException(
                    "business confirmation has an invalid authoritative "
                            + "scope");
        };
    }

    @Override
    public TrustedInboxScopeResolver resolverForPermanentAssetFact(
            String hardwareSn,
            Instant occurredAt) {
        String trustedHardwareSn = requireHardwareSn(hardwareSn);
        return writer -> {
            List<PermanentAssetFactScope> rows = jdbc.query("""
                            SELECT tenant_id, organization_id,
                                   organization_assigned_at
                            FROM dev_device_asset
                            WHERE hardware_sn = ?
                            """,
                    (rs, ignored) -> new PermanentAssetFactScope(
                            (Long) rs.getObject("tenant_id"),
                            (Long) rs.getObject("organization_id"),
                            rs.getObject(
                                    "organization_assigned_at",
                                    LocalDateTime.class)),
                    trustedHardwareSn);
            if (rows.size() != 1) {
                throw new UntrustedInboxSourceException(
                        "authenticated device asset is not registered");
            }
            PermanentAssetFactScope scope = rows.getFirst();
            if (scope.organizationId() == null) {
                if (scope.organizationAssignedAt() != null) {
                    throw new UntrustedInboxSourceException(
                            "device asset assignment facts are inconsistent");
                }
                writer.platform();
                return;
            }
            if (scope.tenantId() == null
                    || scope.organizationAssignedAt() == null) {
                throw new UntrustedInboxSourceException(
                        "device asset assignment facts are inconsistent");
            }
            Instant assignedAt = scope.organizationAssignedAt()
                    .toInstant(ZoneOffset.UTC);
            if (occurredAt == null || occurredAt.isBefore(assignedAt)) {
                writer.platform();
                return;
            }
            writer.organization(scope.tenantId(), scope.organizationId());
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

    private static String requireConfirmationUid(String value) {
        if (value == null || !value.matches(
                "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                        + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")) {
            throw new UntrustedInboxSourceException(
                    "business confirmation identity is invalid");
        }
        return value;
    }

    private record AssetScope(Long tenantId, Long organizationId) {
    }

    private record PermanentAssetFactScope(
            Long tenantId,
            Long organizationId,
            LocalDateTime organizationAssignedAt) {
    }

    private record ConfirmationScope(
            String scopeKind,
            Long tenantId,
            Long organizationId) {
    }
}
