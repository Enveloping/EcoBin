package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.clean.CleanRecordEditService;
import org.enveloping.ecobin.recycling.application.clean.CleanRecordQueryService;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordChange;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordItem;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.EditCleanRecordRequest;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.EditCleanRecordResult;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.WebCleanRecordDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class WebCleanRecordController {

    private final CleanRecordQueryService queryService;
    private final CleanRecordEditService editService;

    public WebCleanRecordController(
            CleanRecordQueryService queryService,
            CleanRecordEditService editService) {
        this.queryService = queryService;
        this.editService = editService;
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-records")
    public TargetApiEnvelope<CursorPage<CleanRecordItem>> staffRecords(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String resultKind,
            @RequestParam(required = false) UUID cleanerUserUid,
            @RequestParam(required = false) String deploymentCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false) String removedBagQr,
            @RequestParam(required = false) String installedBagQr,
            @RequestParam(required = false) String anomalyCode,
            @RequestParam(required = false) String photoCompleteness,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webRecords(
                        false,
                        null,
                        organizationCode,
                        cursor,
                        limit,
                        resultKind,
                        cleanerUserUid,
                        deploymentCode,
                        portNo,
                        removedBagQr,
                        installedBagQr,
                        anomalyCode,
                        photoCompleteness,
                        occurredFrom,
                        occurredTo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-records")
    public TargetApiEnvelope<CursorPage<CleanRecordItem>> platformRecords(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String resultKind,
            @RequestParam(required = false) UUID cleanerUserUid,
            @RequestParam(required = false) String deploymentCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false) String removedBagQr,
            @RequestParam(required = false) String installedBagQr,
            @RequestParam(required = false) String anomalyCode,
            @RequestParam(required = false) String photoCompleteness,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webRecords(
                        true,
                        tenantCode,
                        organizationCode,
                        cursor,
                        limit,
                        resultKind,
                        cleanerUserUid,
                        deploymentCode,
                        portNo,
                        removedBagQr,
                        installedBagQr,
                        anomalyCode,
                        photoCompleteness,
                        occurredFrom,
                        occurredTo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}")
    public TargetApiEnvelope<WebCleanRecordDetail> staffRecord(
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webRecord(
                        false,
                        null,
                        organizationCode,
                        cleanRecordNo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}")
    public TargetApiEnvelope<WebCleanRecordDetail> platformRecord(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webRecord(
                        true,
                        tenantCode,
                        organizationCode,
                        cleanRecordNo),
                TargetRequestIds.resolve(request));
    }

    @PatchMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}")
    public TargetApiEnvelope<EditCleanRecordResult> staffEdit(
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @Valid @RequestBody EditCleanRecordRequest body,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                editService.edit(
                        false,
                        null,
                        organizationCode,
                        cleanRecordNo,
                        idempotencyKey,
                        body),
                TargetRequestIds.resolve(request));
    }

    @PatchMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}")
    public TargetApiEnvelope<EditCleanRecordResult> platformEdit(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @Valid @RequestBody EditCleanRecordRequest body,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                editService.edit(
                        true,
                        tenantCode,
                        organizationCode,
                        cleanRecordNo,
                        idempotencyKey,
                        body),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}/changes")
    public TargetApiEnvelope<CursorPage<CleanRecordChange>> staffChanges(
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.changes(
                        false,
                        null,
                        organizationCode,
                        cleanRecordNo,
                        cursor,
                        limit),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/clean-records/{cleanRecordNo}/changes")
    public TargetApiEnvelope<CursorPage<CleanRecordChange>> platformChanges(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String cleanRecordNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.changes(
                        true,
                        tenantCode,
                        organizationCode,
                        cleanRecordNo,
                        cursor,
                        limit),
                TargetRequestIds.resolve(request));
    }
}
