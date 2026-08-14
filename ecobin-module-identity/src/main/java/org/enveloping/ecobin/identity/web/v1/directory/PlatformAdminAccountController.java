package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.directory.TargetPlatformAdminAccountService;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.AccountVersionCommand;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ChangeOwnPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.ResetPasswordRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.CreatePlatformAdminRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.DeletePlatformAdminRequest;
import org.enveloping.ecobin.identity.web.v1.directory.PlatformAdminModels.PlatformAdminView;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/admin-accounts")
public class PlatformAdminAccountController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final TargetPlatformAdminAccountService accounts;

    public PlatformAdminAccountController(
            TargetPlatformAdminAccountService accounts) {
        this.accounts = accounts;
    }

    @GetMapping
    public TargetApiEnvelope<PageData<PlatformAdminView>> administrators(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(accounts.listAdministrators(
                page, pageSize, status, query), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<PlatformAdminView>> create(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreatePlatformAdminRequest body,
            HttpServletRequest request) {
        PlatformAdminView created = accounts.createAdministrator(
                operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/admin-accounts/"
                                + created.platformAdminUid()))
                .body(ok(created, request));
    }

    @GetMapping("/{platformAdminUid}")
    public TargetApiEnvelope<PlatformAdminView> administrator(
            @PathVariable UUID platformAdminUid,
            HttpServletRequest request) {
        return ok(accounts.getAdministrator(platformAdminUid), request);
    }

    @PostMapping("/{platformAdminUid}/activations")
    public TargetApiEnvelope<PlatformAdminView> activate(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID platformAdminUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(accounts.changeStatus(
                operationUid, platformAdminUid, body, true), request);
    }

    @PostMapping("/{platformAdminUid}/deactivations")
    public TargetApiEnvelope<PlatformAdminView> deactivate(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID platformAdminUid,
            @Valid @RequestBody AccountVersionCommand body,
            HttpServletRequest request) {
        return ok(accounts.changeStatus(
                operationUid, platformAdminUid, body, false), request);
    }

    @PostMapping("/{platformAdminUid}/password-resets")
    public TargetApiEnvelope<PlatformAdminView> resetPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID platformAdminUid,
            @Valid @RequestBody ResetPasswordRequest body,
            HttpServletRequest request) {
        return ok(accounts.resetPassword(
                operationUid, platformAdminUid, body), request);
    }

    @PostMapping("/{platformAdminUid}/deletions")
    public TargetApiEnvelope<PlatformAdminView> delete(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID platformAdminUid,
            @Valid @RequestBody DeletePlatformAdminRequest body,
            HttpServletRequest request) {
        return ok(accounts.deleteAdministrator(
                operationUid, platformAdminUid, body), request);
    }

    @PostMapping("/current/password-changes")
    public TargetApiEnvelope<PlatformAdminView> changeOwnPassword(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody ChangeOwnPasswordRequest body,
            HttpServletRequest request) {
        return ok(accounts.changeOwnPassword(operationUid, body), request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
