package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.identity.application.directory.TargetOrganizationUserBindingService;
import org.enveloping.ecobin.identity.application.web.TargetWebActorContext;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.OrganizationUserView;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/organizations/{organizationCode}"
        + "/organization-users")
public class StaffOrganizationUserController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final TargetOrganizationUserBindingService service;

    public StaffOrganizationUserController(
            TargetOrganizationUserBindingService service) {
        this.service = service;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<OrganizationUserView>>>
            list(
                    @PathVariable String organizationCode,
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    @RequestParam(required = false) String status,
                    @RequestParam(required = false) Boolean phoneBound,
                    @RequestParam(required = false)
                    @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
                    Instant registeredFrom,
                    @RequestParam(required = false)
                    @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
                    Instant registeredTo,
                    @RequestParam(required = false)
                    String sourceDeploymentCode,
                    @RequestParam(required = false)
                    Boolean cleanOperation,
                    HttpServletRequest request) {
        return noStore(service.listOrganizationUsers(
                tenantCode(),
                organizationCode,
                page,
                pageSize,
                status,
                phoneBound,
                registeredFrom,
                registeredTo,
                sourceDeploymentCode,
                cleanOperation), request);
    }

    @GetMapping("/{organizationUserUid}")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserView>> detail(
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            HttpServletRequest request) {
        return noStore(service.organizationUser(
                tenantCode(),
                organizationCode,
                organizationUserUid), request);
    }

    @PostMapping("/{organizationUserUid}/freezes")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserView>> freeze(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return noStore(service.freezeOrganizationUser(
                operationUid,
                tenantCode(),
                organizationCode,
                organizationUserUid,
                body), request);
    }

    @PostMapping("/{organizationUserUid}/restorations")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserView>> restore(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return noStore(service.restoreOrganizationUser(
                operationUid,
                tenantCode(),
                organizationCode,
                organizationUserUid,
                body), request);
    }

    @PostMapping("/{organizationUserUid}"
            + "/capabilities/clean-operation/grants")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserView>>
            grantCleanOperation(
                    @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
                    @PathVariable String organizationCode,
                    @PathVariable UUID organizationUserUid,
                    @Valid @RequestBody AccountVersionCommand body,
                    HttpServletRequest request) {
        return noStore(service.grantCleanOperation(
                operationUid,
                tenantCode(),
                organizationCode,
                organizationUserUid,
                body), request);
    }

    @PostMapping("/{organizationUserUid}"
            + "/capabilities/clean-operation/revocations")
    public ResponseEntity<TargetApiEnvelope<OrganizationUserView>>
            revokeCleanOperation(
                    @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
                    @PathVariable String organizationCode,
                    @PathVariable UUID organizationUserUid,
                    @Valid @RequestBody AccountVersionCommand body,
                    HttpServletRequest request) {
        return noStore(service.revokeCleanOperation(
                operationUid,
                tenantCode(),
                organizationCode,
                organizationUserUid,
                body), request);
    }

    private static String tenantCode() {
        return TargetWebActorContext.required().tenantCode();
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
