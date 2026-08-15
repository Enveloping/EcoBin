package org.enveloping.ecobin.identity.web.v1.directory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.identity.application.directory.FactoryOperatorService;
import org.enveloping.ecobin.identity.web.v1.directory.DirectoryModels.PageData;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.CreateFactoryOperatorRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryBindingIntentCreated;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryBindingRevocationRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryOperatorStatusRequest;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.FactoryOperatorView;
import org.enveloping.ecobin.identity.web.v1.directory.FactoryOperatorModels.UpdateFactoryOperatorRequest;
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

@RestController
@RequestMapping("/api/v1/web/platform/factory-operators")
public class FactoryOperatorController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final FactoryOperatorService operators;

    public FactoryOperatorController(FactoryOperatorService operators) {
        this.operators = operators;
    }

    @GetMapping
    public TargetApiEnvelope<PageData<FactoryOperatorView>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String query,
            HttpServletRequest request) {
        return ok(operators.list(page, pageSize, status, query), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<FactoryOperatorView>> create(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateFactoryOperatorRequest body,
            HttpServletRequest request) {
        FactoryOperatorView created = operators.create(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/factory-operators/"
                                + created.factoryOperatorUid()))
                .body(ok(created, request));
    }

    @GetMapping("/{factoryOperatorUid}")
    public TargetApiEnvelope<FactoryOperatorView> get(
            @PathVariable UUID factoryOperatorUid,
            HttpServletRequest request) {
        return ok(operators.get(factoryOperatorUid), request);
    }

    @PutMapping("/{factoryOperatorUid}")
    public TargetApiEnvelope<FactoryOperatorView> update(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID factoryOperatorUid,
            @Valid @RequestBody UpdateFactoryOperatorRequest body,
            HttpServletRequest request) {
        return ok(operators.update(
                operationUid, factoryOperatorUid, body), request);
    }

    @PostMapping("/{factoryOperatorUid}/activations")
    public TargetApiEnvelope<FactoryOperatorView> activate(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID factoryOperatorUid,
            @Valid @RequestBody FactoryOperatorStatusRequest body,
            HttpServletRequest request) {
        return ok(operators.changeStatus(
                operationUid, factoryOperatorUid, body, true), request);
    }

    @PostMapping("/{factoryOperatorUid}/deactivations")
    public TargetApiEnvelope<FactoryOperatorView> deactivate(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID factoryOperatorUid,
            @Valid @RequestBody FactoryOperatorStatusRequest body,
            HttpServletRequest request) {
        return ok(operators.changeStatus(
                operationUid, factoryOperatorUid, body, false), request);
    }

    @PostMapping("/{factoryOperatorUid}/miniapp-binding-intents")
    public ResponseEntity<TargetApiEnvelope<FactoryBindingIntentCreated>>
            createBindingIntent(
            @PathVariable UUID factoryOperatorUid,
            HttpServletRequest request) {
        return ResponseEntity.status(201).body(ok(
                operators.createBindingIntent(factoryOperatorUid), request));
    }

    @PostMapping("/{factoryOperatorUid}/miniapp-binding-revocations")
    public TargetApiEnvelope<FactoryOperatorView> revokeBinding(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID factoryOperatorUid,
            @Valid @RequestBody FactoryBindingRevocationRequest body,
            HttpServletRequest request) {
        return ok(operators.revokeBinding(
                operationUid, factoryOperatorUid, body), request);
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
