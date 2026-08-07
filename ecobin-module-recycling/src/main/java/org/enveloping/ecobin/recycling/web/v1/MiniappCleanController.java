package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.clean.CleanQueryService;
import org.enveloping.ecobin.recycling.application.clean.CleanRecordQueryService;
import org.enveloping.ecobin.recycling.application.clean.StartCleanOperationService;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.CleanRecordItem;
import org.enveloping.ecobin.recycling.web.v1.CleanRecordModels.MiniappCleanRecordDetail;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOperationAccepted;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOperationView;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanOptionsView;
import org.enveloping.ecobin.recycling.web.v1.CleanModels.StartCleanOperationRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

@RestController
public class MiniappCleanController {

    private final StartCleanOperationService startService;
    private final CleanQueryService queryService;
    private final CleanRecordQueryService recordQueryService;

    public MiniappCleanController(
            StartCleanOperationService startService,
            CleanQueryService queryService,
            CleanRecordQueryService recordQueryService) {
        this.startService = startService;
        this.queryService = queryService;
        this.recordQueryService = recordQueryService;
    }

    @GetMapping(
            "/api/v1/miniapp/devices/{deviceCode}"
                    + "/clean-options")
    public TargetApiEnvelope<CleanOptionsView> cleanOptions(
            @PathVariable String deviceCode,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.options(deviceCode),
                TargetRequestIds.resolve(request));
    }

    @GetMapping("/api/v1/miniapp/clean-operations/{operationUid}")
    public TargetApiEnvelope<CleanOperationView> cleanOperation(
            @PathVariable UUID operationUid,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.operation(operationUid),
                TargetRequestIds.resolve(request));
    }

    @GetMapping("/api/v1/miniapp/me/clean-records")
    public TargetApiEnvelope<CursorPage<CleanRecordItem>> cleanRecords(
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                recordQueryService.miniappRecords(cursor, limit),
                TargetRequestIds.resolve(request));
    }

    @GetMapping("/api/v1/miniapp/me/clean-records/{cleanRecordNo}")
    public TargetApiEnvelope<MiniappCleanRecordDetail> cleanRecord(
            @PathVariable String cleanRecordNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                recordQueryService.miniappRecord(cleanRecordNo),
                TargetRequestIds.resolve(request));
    }

    @PostMapping(
            "/api/v1/miniapp/devices/{deviceCode}"
                    + "/ports/{portNo}/clean-operations")
    public ResponseEntity<TargetApiEnvelope<CleanOperationAccepted>> start(
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @Valid @RequestBody StartCleanOperationRequest body,
            HttpServletRequest request) {
        CleanOperationAccepted accepted = startService.start(
                idempotencyKey,
                deviceCode,
                portNo,
                body.installedBagQr());
        return ResponseEntity.accepted()
                .location(URI.create(accepted.statusUrl()))
                .body(TargetApiEnvelope.ok(
                        accepted,
                        TargetRequestIds.resolve(request)));
    }
}
