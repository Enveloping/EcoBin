package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.directory.TargetOrganizationUserBindingService;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserLookupRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserLookupView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.SetStaffMiniappBindingRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffMiniappBindingLookupView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.StaffMiniappBindingView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.VersionCommand;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/tenants/{tenantCode}"
        + "/organizations/{organizationCode}")
public class PlatformOrganizationUserBindingController {

    private final TargetOrganizationUserBindingService bindingService;

    public PlatformOrganizationUserBindingController(
            TargetOrganizationUserBindingService bindingService) {
        this.bindingService = bindingService;
    }

    @PostMapping("/organization-users/phone-lookups")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserLookupView>>
            lookup(
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @Valid @RequestBody OrganizationUserLookupRequest body,
                    HttpServletRequest request) {
        return noStore(bindingService.lookupByPhone(
                tenantCode, organizationCode, body.phoneNumber()), request);
    }

    @GetMapping("/staff-accounts/{staffUid}/miniapp-binding")
    public ResponseEntity<TargetApiEnvelope<StaffMiniappBindingLookupView>>
            current(
                    @PathVariable String tenantCode,
                    @PathVariable String organizationCode,
                    @PathVariable UUID staffUid,
                    HttpServletRequest request) {
        return noStore(bindingService.currentStaffBinding(
                tenantCode, organizationCode, staffUid), request);
    }

    @PutMapping("/staff-accounts/{staffUid}/miniapp-binding")
    public TargetApiEnvelope<StaffMiniappBindingView> set(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID staffUid,
            @RequestBody JsonNode body,
            HttpServletRequest request) {
        SetStaffMiniappBindingRequest parsed =
                StaffMiniappBindingRequestParser.parse(body);
        return ok(bindingService.setBinding(
                operationUid,
                tenantCode,
                organizationCode,
                staffUid,
                parsed), request);
    }

    @PostMapping("/staff-miniapp-bindings/{bindingUid}/revocations")
    public TargetApiEnvelope<StaffMiniappBindingView> revoke(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID bindingUid,
            @Valid @RequestBody VersionCommand body,
            HttpServletRequest request) {
        return ok(bindingService.revokeBinding(
                operationUid,
                tenantCode,
                organizationCode,
                bindingUid,
                body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(data, request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
