package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.directory.TargetIdentityDirectoryService;
import org.enveloping.ecobin.identity.application.web.TargetWebActor;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
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
@RequestMapping("/api/v1/web")
public class StaffIdentityDirectoryController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final TargetIdentityDirectoryService directory;

    public StaffIdentityDirectoryController(
            TargetIdentityDirectoryService directory) {
        this.directory = directory;
    }

    @GetMapping("/tenants/current")
    public TargetApiEnvelope<TenantView> currentTenant(
            HttpServletRequest request) {
        return ok(directory.getTenant(actor().tenantCode()), request);
    }

    @PutMapping("/tenants/current/profile")
    public TargetApiEnvelope<TenantView> updateCurrentTenant(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody UpdateTenantProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateTenantProfile(
                operationUid, actor().tenantCode(), body), request);
    }

    @GetMapping("/organizations")
    public TargetApiEnvelope<PageData<OrganizationView>> organizations(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(directory.listOrganizations(
                actor().tenantCode(), page, pageSize, status, query), request);
    }

    @PostMapping("/organizations")
    public ResponseEntity<TargetApiEnvelope<OrganizationView>> createOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateOrganizationRequest body,
            HttpServletRequest request) {
        OrganizationView created = directory.createOrganization(
                operationUid, actor().tenantCode(), body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/organizations/"
                                + created.organizationCode()))
                .body(ok(created, request));
    }

    @GetMapping("/organizations/{organizationCode}")
    public TargetApiEnvelope<OrganizationView> organization(
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(directory.getOrganization(
                actor().tenantCode(), organizationCode), request);
    }

    @PutMapping("/organizations/{organizationCode}/profile")
    public TargetApiEnvelope<OrganizationView> updateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @Valid @RequestBody UpdateOrganizationProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateOrganizationProfile(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                body), request);
    }

    @PostMapping("/organizations/{organizationCode}/activations")
    public TargetApiEnvelope<OrganizationView> activateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.activateOrganization(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                body), request);
    }

    @PostMapping("/organizations/{organizationCode}/deactivations")
    public TargetApiEnvelope<OrganizationView> deactivateOrganization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(directory.deactivateOrganization(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                body), request);
    }

    @GetMapping("/staff-accounts")
    public TargetApiEnvelope<PageData<StaffAccountView>> staffAccounts(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(directory.listStaff(
                actor().tenantCode(), page, pageSize, status, query), request);
    }

    @PostMapping("/organizations/{organizationCode}/staff-account-provisionings")
    public ResponseEntity<TargetApiEnvelope<ProvisionedOrganizationStaffView>>
            provisionOrganizationStaff(
                    @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
                    @PathVariable String organizationCode,
                    @Valid @RequestBody ProvisionOrganizationStaffRequest body,
                    HttpServletRequest request) {
        TargetWebActor actor = actor();
        ProvisionedOrganizationStaffView created =
                directory.provisionOrganizationStaff(
                        operationUid,
                        actor.tenantCode(),
                        organizationCode,
                        body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/staff-accounts/"
                                + created.staffAccount().staffAccountUid()))
                .body(ok(created, request));
    }

    @PostMapping("/staff-accounts")
    public ResponseEntity<TargetApiEnvelope<StaffAccountView>> createStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateStaffAccountRequest body,
            HttpServletRequest request) {
        StaffAccountView created = directory.createStaff(
                operationUid, actor().tenantCode(), body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/staff-accounts/"
                                + created.staffAccountUid()))
                .body(ok(created, request));
    }

    @GetMapping("/staff-accounts/{staffUid}")
    public TargetApiEnvelope<StaffAccountView> staffAccount(
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.getStaff(
                actor().tenantCode(), staffUid), request);
    }

    @PutMapping("/staff-accounts/{staffUid}/profile")
    public TargetApiEnvelope<StaffAccountView> updateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID staffUid,
            @Valid @RequestBody UpdateStaffProfileRequest body,
            HttpServletRequest request) {
        return ok(directory.updateStaffProfile(
                operationUid,
                actor().tenantCode(),
                staffUid,
                body,
                false), request);
    }

    @PostMapping("/staff-accounts/{staffUid}/activations")
    public TargetApiEnvelope<StaffAccountView> activateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.changeStaffStatus(
                operationUid,
                actor().tenantCode(),
                staffUid,
                body,
                true), request);
    }

    @PostMapping("/staff-accounts/{staffUid}/deactivations")
    public TargetApiEnvelope<StaffAccountView> deactivateStaff(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.changeStaffStatus(
                operationUid,
                actor().tenantCode(),
                staffUid,
                body,
                false), request);
    }

    @PostMapping("/staff-accounts/{staffUid}/password-resets")
    public TargetApiEnvelope<StaffAccountView> resetStaffPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ResetPasswordRequest body,
            HttpServletRequest request) {
        return ok(directory.resetStaffPassword(
                operationUid, actor().tenantCode(), staffUid, body), request);
    }

    @PutMapping("/staff-accounts/current/profile")
    public TargetApiEnvelope<StaffAccountView> updateOwnProfile(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody UpdateStaffProfileRequest body,
            HttpServletRequest request) {
        TargetWebActor actor = actor();
        return ok(directory.updateStaffProfile(
                operationUid,
                actor.tenantCode(),
                actor.principalUid(),
                body,
                true), request);
    }

    @PostMapping("/staff-accounts/current/password-changes")
    public TargetApiEnvelope<StaffAccountView> changeOwnPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody ChangeOwnPasswordRequest body,
            HttpServletRequest request) {
        return ok(directory.changeOwnPassword(operationUid, body), request);
    }

    @GetMapping("/permission-definitions")
    public TargetApiEnvelope<?> permissionDefinitions(
            HttpServletRequest request) {
        return ok(directory.permissionDefinitions(), request);
    }

    @GetMapping("/staff-accounts/current/effective-access")
    public TargetApiEnvelope<EffectiveAccessView> ownEffectiveAccess(
            HttpServletRequest request) {
        TargetWebActor actor = actor();
        return ok(directory.effectiveAccess(
                actor.tenantCode(), actor.principalUid(), true), request);
    }

    @GetMapping("/staff-accounts/{staffUid}/effective-access")
    public TargetApiEnvelope<EffectiveAccessView> effectiveAccess(
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.effectiveAccess(
                actor().tenantCode(), staffUid, false), request);
    }

    @PutMapping("/staff-accounts/{staffUid}/tenant-permissions")
    public TargetApiEnvelope<EffectiveAccessView> replaceTenantPermissions(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ReplaceTenantPermissionsRequest body,
            HttpServletRequest request) {
        return ok(directory.replaceTenantPermissions(
                operationUid,
                actor().tenantCode(),
                staffUid,
                body), request);
    }

    @GetMapping("/organizations/{organizationCode}/staff-memberships")
    public TargetApiEnvelope<PageData<MembershipView>> memberships(
            @PathVariable String organizationCode,
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            HttpServletRequest request) {
        return ok(directory.listMemberships(
                actor().tenantCode(),
                organizationCode,
                page,
                pageSize), request);
    }

    @PostMapping("/organizations/{organizationCode}/staff-memberships")
    public ResponseEntity<TargetApiEnvelope<MembershipView>> createMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @Valid @RequestBody CreateMembershipRequest body,
            HttpServletRequest request) {
        MembershipView created = directory.createMembership(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/organizations/" + organizationCode
                                + "/staff-memberships/"
                                + created.staffAccountUid()))
                .body(ok(created, request));
    }

    @GetMapping("/organizations/{organizationCode}/staff-memberships/{staffUid}")
    public TargetApiEnvelope<MembershipView> membership(
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            HttpServletRequest request) {
        return ok(directory.getMembership(
                actor().tenantCode(), organizationCode, staffUid), request);
    }

    @PutMapping("/organizations/{organizationCode}/staff-memberships/{staffUid}/authorization")
    public TargetApiEnvelope<MembershipView> updateMembershipAuthorization(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody MembershipAuthorizationRequest body,
            HttpServletRequest request) {
        return ok(directory.replaceMembershipAuthorization(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                staffUid,
                body), request);
    }

    @PostMapping("/organizations/{organizationCode}/staff-memberships/{staffUid}/activations")
    public TargetApiEnvelope<MembershipView> activateMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody ActivateMembershipRequest body,
            HttpServletRequest request) {
        return ok(directory.activateMembership(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                staffUid,
                body), request);
    }

    @PostMapping("/organizations/{organizationCode}/staff-memberships/{staffUid}/deactivations")
    public TargetApiEnvelope<MembershipView> deactivateMembership(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(directory.deactivateMembership(
                operationUid,
                actor().tenantCode(),
                organizationCode,
                staffUid,
                body), request);
    }

    private static TargetWebActor actor() {
        return TargetWebActorContext.required();
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
