package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public final class DirectoryModels {

    private DirectoryModels() {
    }

    public record PageData<T>(
            List<T> items,
            int page,
            int pageSize,
            long total) {
    }

    public record PrincipalAccountSummary(
            UUID staffAccountUid,
            String status,
            long version,
            long authVersion) {
    }

    public record TenantView(
            String tenantCode,
            String enterpriseName,
            String status,
            String contactName,
            String contactPhone,
            String contactAddress,
            long version,
            PrincipalAccountSummary principalAccount,
            Instant createdAt,
            Instant updatedAt) {
    }

    public record OrganizationView(
            String organizationCode,
            String organizationName,
            String status,
            String contactPhone,
            String contactAddress,
            long version,
            Instant createdAt,
            Instant updatedAt) {
    }

    public record StaffAccountView(
            UUID staffAccountUid,
            String accountKind,
            String loginName,
            String displayName,
            String contactPhone,
            String status,
            long version,
            long authVersion,
            Instant createdAt,
            Instant updatedAt) {
    }

    public record PermissionDefinitionView(
            String permissionCode,
            String scopeKind,
            String permissionName,
            String description) {
    }

    public record OrganizationEffectiveAccess(
            String organizationCode,
            String organizationName,
            boolean manager,
            List<String> permissionCodes) {
    }

    public record EffectiveAccessView(
            UUID staffAccountUid,
            List<String> tenantPermissionCodes,
            List<OrganizationEffectiveAccess> organizations,
            long authVersion) {
    }

    public record MembershipView(
            String organizationCode,
            UUID staffAccountUid,
            String displayName,
            boolean manager,
            String status,
            List<String> permissionCodes,
            long version,
            long authVersion,
            Instant createdAt,
            Instant updatedAt) {
    }

    public record CreateTenantRequest(
            @NotBlank
            @Size(max = 32)
            @Pattern(regexp = "^[a-z0-9][a-z0-9-]*$")
            String tenantCode,
            @NotBlank @Size(max = 200) String enterpriseName,
            @Size(max = 100) String contactName,
            @Size(max = 32) String contactPhone,
            @Size(max = 500) String contactAddress) {
    }

    public record UpdateTenantProfileRequest(
            @NotBlank @Size(max = 200) String enterpriseName,
            @Size(max = 100) String contactName,
            @Size(max = 32) String contactPhone,
            @Size(max = 500) String contactAddress,
            @PositiveOrZero long expectedVersion) {
    }

    public record CreatePrincipalAccountRequest(
            @NotBlank
            @Size(max = 64)
            @Pattern(regexp = "^[a-z0-9][a-z0-9._-]*$")
            String loginName,
            @NotBlank @Size(min = 8, max = 256) String initialPassword,
            @NotBlank @Size(max = 100) String displayName,
            @Size(max = 32) String contactPhone,
            @PositiveOrZero long expectedVersion) {
    }

    public record CreateOrganizationRequest(
            @NotBlank
            @Size(max = 32)
            @Pattern(regexp = "^[a-z0-9][a-z0-9-]*$")
            String organizationCode,
            @NotBlank @Size(max = 200) String organizationName,
            @Size(max = 32) String contactPhone,
            @Size(max = 500) String contactAddress) {
    }

    public record UpdateOrganizationProfileRequest(
            @NotBlank @Size(max = 200) String organizationName,
            @Size(max = 32) String contactPhone,
            @Size(max = 500) String contactAddress,
            @PositiveOrZero long expectedVersion) {
    }

    public record VersionCommand(
            @PositiveOrZero long expectedVersion,
            @Size(max = 500) String reason) {
    }

    public record AccountVersionCommand(
            @PositiveOrZero long expectedVersion,
            @PositiveOrZero long expectedAuthVersion,
            @Size(max = 500) String reason) {
    }

    public record CreateStaffAccountRequest(
            @NotBlank
            @Size(max = 64)
            @Pattern(regexp = "^[a-z0-9][a-z0-9._-]*$")
            String loginName,
            @NotBlank @Size(min = 8, max = 256) String initialPassword,
            @NotBlank @Size(max = 100) String displayName,
            @Size(max = 32) String contactPhone,
            @NotNull List<@NotBlank String> permissionCodes) {

        public CreateStaffAccountRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record ProvisionOrganizationStaffRequest(
            @NotBlank
            @Size(max = 64)
            @Pattern(regexp = "^[a-z0-9][a-z0-9._-]*$")
            String loginName,
            @NotBlank @Size(min = 8, max = 256) String initialPassword,
            @NotBlank @Size(max = 100) String displayName,
            @Size(max = 32) String contactPhone,
            boolean manager,
            @NotNull List<@NotBlank String> permissionCodes) {

        public ProvisionOrganizationStaffRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record ProvisionedOrganizationStaffView(
            StaffAccountView staffAccount,
            MembershipView membership) {
    }

    public record UpdateStaffProfileRequest(
            @NotBlank @Size(max = 100) String displayName,
            @Size(max = 32) String contactPhone,
            @PositiveOrZero long expectedVersion) {
    }

    public record ResetPasswordRequest(
            @NotBlank @Size(min = 8, max = 256) String newPassword,
            @PositiveOrZero long expectedVersion,
            @PositiveOrZero long expectedAuthVersion) {
    }

    public record ChangeOwnPasswordRequest(
            @NotBlank @Size(max = 256) String currentPassword,
            @NotBlank @Size(min = 8, max = 256) String newPassword,
            @PositiveOrZero long expectedVersion,
            @PositiveOrZero long expectedAuthVersion) {
    }

    public record ReplaceTenantPermissionsRequest(
            @NotNull List<@NotBlank String> permissionCodes,
            @PositiveOrZero long expectedAuthVersion) {

        public ReplaceTenantPermissionsRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record CreateMembershipRequest(
            @NotNull UUID staffAccountUid,
            boolean manager,
            @NotNull List<@NotBlank String> permissionCodes,
            @PositiveOrZero long expectedAuthVersion) {

        public CreateMembershipRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record MembershipAuthorizationRequest(
            boolean manager,
            @NotNull List<@NotBlank String> permissionCodes,
            @PositiveOrZero long expectedVersion,
            @PositiveOrZero long expectedAuthVersion) {

        public MembershipAuthorizationRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record ActivateMembershipRequest(
            boolean manager,
            @NotNull List<@NotBlank String> permissionCodes,
            @PositiveOrZero long expectedVersion,
            @PositiveOrZero long expectedAuthVersion,
            @Size(max = 500) String reason) {

        public ActivateMembershipRequest {
            permissionCodes = permissionCodes == null
                    ? List.of() : List.copyOf(permissionCodes);
        }
    }

    public record SafeChange(
            String fingerprint,
            Object before,
            Object after,
            Object response,
            Map<String, Object> metadata) {
    }
}
