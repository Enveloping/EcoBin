package org.enveloping.ecobin.operations.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.operations.application.governance.GovernanceQueryService;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AuditLogView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.CursorPage;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class AuditController {

    private final GovernanceQueryService service;

    public AuditController(GovernanceQueryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/web/audit-logs")
    public ResponseEntity<TargetApiEnvelope<CursorPage<AuditLogView>>> logs(
            @RequestParam(required = false) String scopeKind,
            @RequestParam(required = false) String organizationCode,
            @RequestParam(required = false) String actorKind,
            @RequestParam(required = false) UUID actorUid,
            @RequestParam(required = false) String actionCode,
            @RequestParam(required = false) String result,
            @RequestParam(required = false) String targetType,
            @RequestParam(required = false) String targetKey,
            @RequestParam(required = false) UUID requestUid,
            @RequestParam(required = false) UUID operationUid,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.auditLogs(false, scopeKind, organizationCode,
                actorKind, actorUid, actionCode, result, targetType,
                targetKey, requestUid, operationUid, occurredFrom,
                occurredTo, cursor, limit), request);
    }

    @GetMapping("/api/v1/web/platform/audit-logs")
    public ResponseEntity<TargetApiEnvelope<CursorPage<AuditLogView>>>
    platformLogs(
            @RequestParam(required = false) String scopeKind,
            @RequestParam(required = false) String organizationCode,
            @RequestParam(required = false) String actorKind,
            @RequestParam(required = false) UUID actorUid,
            @RequestParam(required = false) String actionCode,
            @RequestParam(required = false) String result,
            @RequestParam(required = false) String targetType,
            @RequestParam(required = false) String targetKey,
            @RequestParam(required = false) UUID requestUid,
            @RequestParam(required = false) UUID operationUid,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.auditLogs(true, scopeKind, organizationCode,
                actorKind, actorUid, actionCode, result, targetType,
                targetKey, requestUid, operationUid, occurredFrom,
                occurredTo, cursor, limit), request);
    }

    @GetMapping("/api/v1/web/audit-logs/{auditUid}")
    public ResponseEntity<TargetApiEnvelope<AuditLogView>> log(
            @PathVariable UUID auditUid, HttpServletRequest request) {
        return ok(service.auditLog(false, auditUid), request);
    }

    @GetMapping("/api/v1/web/platform/audit-logs/{auditUid}")
    public ResponseEntity<TargetApiEnvelope<AuditLogView>> platformLog(
            @PathVariable UUID auditUid, HttpServletRequest request) {
        return ok(service.auditLog(true, auditUid), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T value, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        value, TargetRequestIds.resolve(request)));
    }
}
