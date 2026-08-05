package org.enveloping.ecobin.operations.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.operations.application.governance.GovernanceQueryService;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AlertView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.PageData;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.VersionedOperationResult;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.VersionedReasonRequest;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class AlertController {

    private final GovernanceQueryService service;

    public AlertController(GovernanceQueryService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/web/alerts")
    public ResponseEntity<TargetApiEnvelope<PageData<AlertView>>> alerts(
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String acknowledgementState,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String alertCode,
            @RequestParam(required = false) String organizationCode,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedTo,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer pageSize,
            HttpServletRequest request) {
        return ok(service.alerts(false, false, state, acknowledgementState,
                severity, category, alertCode, organizationCode,
                firstDetectedFrom, firstDetectedTo, page, pageSize), request);
    }

    @GetMapping("/api/v1/web/platform/alerts")
    public ResponseEntity<TargetApiEnvelope<PageData<AlertView>>> platformAlerts(
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String acknowledgementState,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String alertCode,
            @RequestParam(required = false) String organizationCode,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedTo,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer pageSize,
            HttpServletRequest request) {
        return ok(service.alerts(true, false, state, acknowledgementState,
                severity, category, alertCode, organizationCode,
                firstDetectedFrom, firstDetectedTo, page, pageSize), request);
    }

    @GetMapping("/api/v1/miniapp-staff/alerts")
    public ResponseEntity<TargetApiEnvelope<PageData<AlertView>>> staffAlerts(
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String severity,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer pageSize,
            HttpServletRequest request) {
        return ok(service.alerts(false, true, state, null, severity,
                category, null, null, null, null, page, pageSize), request);
    }

    @GetMapping("/api/v1/web/alerts/{alertUid}")
    public ResponseEntity<TargetApiEnvelope<AlertView>> alert(
            @PathVariable UUID alertUid, HttpServletRequest request) {
        return ok(service.alert(false, false, alertUid), request);
    }

    @GetMapping("/api/v1/web/platform/alerts/{alertUid}")
    public ResponseEntity<TargetApiEnvelope<AlertView>> platformAlert(
            @PathVariable UUID alertUid, HttpServletRequest request) {
        return ok(service.alert(true, false, alertUid), request);
    }

    @GetMapping("/api/v1/miniapp-staff/alerts/{alertUid}")
    public ResponseEntity<TargetApiEnvelope<AlertView>> staffAlert(
            @PathVariable UUID alertUid, HttpServletRequest request) {
        return ok(service.alert(false, true, alertUid), request);
    }

    @PostMapping("/api/v1/web/alerts/{alertUid}/acknowledgements")
    public ResponseEntity<TargetApiEnvelope<VersionedOperationResult>>
    acknowledge(
            @PathVariable UUID alertUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody VersionedReasonRequest body,
            HttpServletRequest request) {
        return ok(service.acknowledgeAlert(
                false, alertUid, operationUid, body), request);
    }

    @PostMapping("/api/v1/web/platform/alerts/{alertUid}/acknowledgements")
    public ResponseEntity<TargetApiEnvelope<VersionedOperationResult>>
    platformAcknowledge(
            @PathVariable UUID alertUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody VersionedReasonRequest body,
            HttpServletRequest request) {
        return ok(service.acknowledgeAlert(
                true, alertUid, operationUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T value, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        value, TargetRequestIds.resolve(request)));
    }
}
