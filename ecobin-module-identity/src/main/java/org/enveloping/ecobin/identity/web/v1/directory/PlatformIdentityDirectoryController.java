package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.directory.TargetIdentityDirectoryService;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

import static org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.*;

@RestController
@RequestMapping("/api/v1/web/platform")
public class PlatformIdentityDirectoryController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final TargetIdentityDirectoryService directory;

    public PlatformIdentityDirectoryController(
            TargetIdentityDirectoryService directory) {
        this.directory = directory;
    }

    @GetMapping("/tenants")
    public TargetApiEnvelope<PageData<TenantView>> tenants(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(directory.listTenants(page, pageSize, status, query), request);
    }

    @PostMapping("/tenants")
    public ResponseEntity<TargetApiEnvelope<TenantView>> createTenant(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateTenantRequest body,
            HttpServletRequest request) {
        TenantView created = directory.createTenant(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + created.tenantCode()))
                .body(ok(created, request));
    }

    @GetMapping("/tenants/{tenantCode}")
    public TargetApiEnvelope<TenantView> tenant(
            @PathVariable String tenantCode,
            HttpServletRequest request) {
        return ok(directory.getTenant(tenantCode), request);
    }

    @PutMapping("/tenants/{tenantCode}/profile")
    public TargetApiEnvelope<TenantView> updateTenant(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody UpdateTenantProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateTenantProfile(
                operationUid, tenantCode, body), request);
    }

    @PostMapping("/tenants/{tenantCode}/principal-account")
    public ResponseEntity<TargetApiEnvelope<StaffAccountView>> createPrincipal(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody CreatePrincipalAccountRequest body,
            HttpServletRequest request) {
        StaffAccountView created = directory.createPrincipalAccount(
                operationUid, tenantCode, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + tenantCode
                                + "/staff-accounts/"
                                + created.staffAccountUid()))
                .body(ok(created, request));
    }

    @PostMapping("/tenants/{tenantCode}/principal-account/password-resets")
    public TargetApiEnvelope<StaffAccountView> resetPrincipalPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody ResetPasswordRequest body,
            HttpServletRequest request) {
        return ok(directory.resetPrincipalPassword(
                operationUid, tenantCode, body), request);
    }

    @PostMapping("/tenants/{tenantCode}/activations")
    public TargetApiEnvelope<TenantView> activateTenant(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.activateTenant(
                operationUid, tenantCode, body), request);
    }

    @PostMapping("/tenants/{tenantCode}/deactivations")
    public TargetApiEnvelope<TenantView> deactivateTenant(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.deactivateTenant(
                operationUid, tenantCode, body), request);
    }

    @GetMapping("/tenants/{tenantCode}/organizations")
    public TargetApiEnvelope<PageData<OrganizationView>> organizations(
            @PathVariable String tenantCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(directory.listOrganizations(
                tenantCode, page, pageSize, status, query), request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations")
    public ResponseEntity<TargetApiEnvelope<OrganizationView>> createOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody CreateOrganizationRequest body,
            HttpServletRequest request) {
        OrganizationView created = directory.createOrganization(
                operationUid, tenantCode, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + tenantCode
                                + "/organizations/"
                                + created.organizationCode()))
                .body(ok(created, request));
    }

    @GetMapping("/tenants/{tenantCode}/organizations/{organizationCode}")
    public TargetApiEnvelope<OrganizationView> organization(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(directory.getOrganization(
                tenantCode, organizationCode), request);
    }

    @PutMapping("/tenants/{tenantCode}/organizations/{organizationCode}/profile")
    public TargetApiEnvelope<OrganizationView> updateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody UpdateOrganizationProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateOrganizationProfile(
                operationUid, tenantCode, organizationCode, body), request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/activations")
    public TargetApiEnvelope<OrganizationView> activateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.activateOrganization(
                operationUid, tenantCode, organizationCode, body), request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/deactivations")
    public TargetApiEnvelope<OrganizationView> deactivateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.deactivateOrganization(
                operationUid, tenantCode, organizationCode, body), request);
    }

    @GetMapping("/tenants/{tenantCode}/staff-accounts")
    public TargetApiEnvelope<PageData<StaffAccountView>> staffAccounts(
            @PathVariable String tenantCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(directory.listStaff(
                tenantCode, page, pageSize, status, query), request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-account-provisionings")
    public ResponseEntity<TargetApiEnvelope<ProvisionedOrganizationStaffView>>
            provisionOrganizationStaff(
                    @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @Valid @RequestBody ProvisionOrganizationStaffRequest body,
                    HttpServletRequest request) {
        ProvisionedOrganizationStaffView created =
                directory.provisionOrganizationStaff(
                        operationUid,
                        tenantCode,
                        organizationCode,
                        body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + tenantCode
                                + "/staff-accounts/"
                                + created.staffAccount().staffAccountUid()))
                .body(ok(created, request));
    }

    @PostMapping("/tenants/{tenantCode}/staff-accounts")
    public ResponseEntity<TargetApiEnvelope<StaffAccountView>> createStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @Valid @RequestBody CreateStaffAccountRequest body,
            HttpServletRequest request) {
        StaffAccountView created = directory.createStaff(
                operationUid, tenantCode, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + tenantCode
                                + "/staff-accounts/"
                                + created.staffAccountUid()))
                .body(ok(created, request));
    }

    @GetMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}")
    public TargetApiEnvelope<StaffAccountView> staffAccount(
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.getStaff(tenantCode, staffUid), request);
    }

    @PutMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/profile")
    public TargetApiEnvelope<StaffAccountView> updateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody UpdateStaffProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateStaffProfile(
                operationUid, tenantCode, staffUid, body, false), request);
    }

    @PostMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/activations")
    public TargetApiEnvelope<StaffAccountView> activateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.changeStaffStatus(
                operationUid, tenantCode, staffUid, body, true), request);
    }

    @PostMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/deactivations")
    public TargetApiEnvelope<StaffAccountView> deactivateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.changeStaffStatus(
                operationUid, tenantCode, staffUid, body, false), request);
    }

    @PostMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/password-resets")
    public TargetApiEnvelope<StaffAccountView> resetStaffPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ResetPasswordRequest body,
            HttpServletRequest request) {
        return ok(directory.resetStaffPassword(
                operationUid, tenantCode, staffUid, body), request);
    }

    @GetMapping("/permission-definitions")
    public TargetApiEnvelope<?> permissionDefinitions(
            HttpServletRequest request) {
        return ok(directory.permissionDefinitions(), request);
    }

    @GetMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/effective-access")
    public TargetApiEnvelope<EffectiveAccessView> effectiveAccess(
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.effectiveAccess(
                tenantCode, staffUid, false), request);
    }

    @PutMapping("/tenants/{tenantCode}/staff-accounts/{staffUid}/tenant-permissions")
    public TargetApiEnvelope<EffectiveAccessView> replaceTenantPermissions(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ReplaceTenantPermissionsRequest body,
            HttpServletRequest request) {
        return ok(directory.replaceTenantPermissions(
                operationUid, tenantCode, staffUid, body), request);
    }

    @GetMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships")
    public TargetApiEnvelope<PageData<MembershipView>> memberships(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            HttpServletRequest request) {
        return ok(directory.listMemberships(
                tenantCode, organizationCode, page, pageSize), request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships")
    public ResponseEntity<TargetApiEnvelope<MembershipView>> createMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @Valid @RequestBody CreateMembershipRequest body,
            HttpServletRequest request) {
        MembershipView created = directory.createMembership(
                operationUid, tenantCode, organizationCode, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/tenants/" + tenantCode
                                + "/organizations/" + organizationCode
                                + "/staff-memberships/"
                                + created.staffAccountUid()))
                .body(ok(created, request));
    }

    @GetMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffUid}")
    public TargetApiEnvelope<MembershipView> membership(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.getMembership(
                tenantCode, organizationCode, staffUid), request);
    }

    @PutMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffUid}/authorization")
    public TargetApiEnvelope<MembershipView> updateMembershipAuthorization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody MembershipAuthorizationRequest body,
            HttpServletRequest request) {
        return ok(directory.replaceMembershipAuthorization(
                operationUid, tenantCode, organizationCode, staffUid, body),
                request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffUid}/activations")
    public TargetApiEnvelope<MembershipView> activateMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ActivateMembershipRequest body,
            HttpServletRequest request) {
        return ok(directory.activateMembership(
                operationUid, tenantCode, organizationCode, staffUid, body),
                request);
    }

    @PostMapping("/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffUid}/deactivations")
    public TargetApiEnvelope<MembershipView> deactivateMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.deactivateMembership(
                operationUid, tenantCode, organizationCode, staffUid, body),
                request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
