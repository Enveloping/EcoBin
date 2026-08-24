package org.enveloping.ecobin.operations.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.operations.application.governance.TechnicalOperationsService;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.AcceptedOperation;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.CursorPage;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.PageData;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.QuarantineView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ReliableTaskView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ReliableTaskTypeView;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.ResumeTaskRequest;
import org.enveloping.ecobin.operations.web.v1.OperationsModels.TaskAttemptView;
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
import java.util.List;
import java.util.UUID;

@RestController
public class PlatformTechnicalOperationsController {

    private final TechnicalOperationsService service;

    public PlatformTechnicalOperationsController(
            TechnicalOperationsService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/web/platform/operations/reliable-tasks")
    public ResponseEntity<TargetApiEnvelope<PageData<ReliableTaskView>>> tasks(
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String executionLane,
            @RequestParam(required = false) String taskKind,
            @RequestParam(required = false) String taskType,
            @RequestParam(required = false) String targetType,
            @RequestParam(required = false) String targetKey,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant createdTo,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer pageSize,
            HttpServletRequest request) {
        return ok(service.tasks(
                state, executionLane, taskKind, taskType, targetType,
                targetKey, createdFrom, createdTo, page, pageSize), request);
    }

    @GetMapping("/api/v1/web/platform/operations/reliable-task-types")
    public ResponseEntity<TargetApiEnvelope<List<ReliableTaskTypeView>>>
    taskTypes(HttpServletRequest request) {
        return ok(service.taskTypes(), request);
    }

    @GetMapping("/api/v1/web/platform/operations/reliable-tasks/{taskUid}")
    public ResponseEntity<TargetApiEnvelope<ReliableTaskView>> task(
            @PathVariable UUID taskUid,
            HttpServletRequest request) {
        return ok(service.task(taskUid), request);
    }

    @GetMapping("/api/v1/web/platform/operations/reliable-tasks/{taskUid}"
            + "/attempts")
    public ResponseEntity<TargetApiEnvelope<CursorPage<TaskAttemptView>>> attempts(
            @PathVariable UUID taskUid,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.attempts(taskUid, cursor, limit), request);
    }

    @PostMapping("/api/v1/web/platform/operations/reliable-tasks/{taskUid}"
            + "/resumptions")
    public ResponseEntity<TargetApiEnvelope<AcceptedOperation>> resume(
            @PathVariable UUID taskUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody ResumeTaskRequest body,
            HttpServletRequest request) {
        return ResponseEntity.accepted()
                .cacheControl(CacheControl.noStore())
                .body(envelope(
                        service.resume(taskUid, operationUid, body), request));
    }

    @GetMapping("/api/v1/web/platform/operations/message-quarantines")
    public ResponseEntity<TargetApiEnvelope<PageData<QuarantineView>>> quarantines(
            @RequestParam(required = false) String state,
            @RequestParam(required = false) String reasonCode,
            @RequestParam(required = false) String sourceNamespace,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant firstDetectedTo,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer pageSize,
            HttpServletRequest request) {
        return ok(service.quarantines(
                state, reasonCode, sourceNamespace,
                firstDetectedFrom, firstDetectedTo, page, pageSize), request);
    }

    @GetMapping("/api/v1/web/platform/operations/message-quarantines/"
            + "{quarantineUid}")
    public ResponseEntity<TargetApiEnvelope<QuarantineView>> quarantine(
            @PathVariable UUID quarantineUid,
            HttpServletRequest request) {
        return ok(service.quarantine(quarantineUid), request);
    }

    @PostMapping("/api/v1/web/platform/operations/message-quarantines/"
            + "{quarantineUid}/acknowledgements")
    public ResponseEntity<TargetApiEnvelope<VersionedOperationResult>>
    acknowledgeQuarantine(
            @PathVariable UUID quarantineUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody VersionedReasonRequest body,
            HttpServletRequest request) {
        return ok(service.acknowledgeQuarantine(
                quarantineUid, operationUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T value, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(envelope(value, request));
    }

    private static <T> TargetApiEnvelope<T> envelope(
            T value, HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                value, TargetRequestIds.resolve(request));
    }
}
