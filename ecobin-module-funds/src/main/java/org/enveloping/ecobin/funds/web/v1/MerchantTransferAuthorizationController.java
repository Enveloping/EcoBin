package org.enveloping.ecobin.funds.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.funds.application.authorization
        .MerchantTransferAuthorizationApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels
        .EmptyMerchantTransferAuthorizationRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels
        .MerchantTransferAuthorizationAcceptedView;
import org.enveloping.ecobin.funds.web.v1.FundsModels
        .MerchantTransferAuthorizationView;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class MerchantTransferAuthorizationController {

    private final MerchantTransferAuthorizationApplicationService service;

    public MerchantTransferAuthorizationController(
            MerchantTransferAuthorizationApplicationService service) {
        this.service = service;
    }

    @GetMapping(
            "/api/v1/miniapp/me/merchant-transfer-authorization")
    public ResponseEntity<TargetApiEnvelope<
            MerchantTransferAuthorizationView>> current(
            HttpServletRequest request) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        service.current(),
                        TargetRequestIds.resolve(request)));
    }

    @PostMapping(
            "/api/v1/miniapp/me/merchant-transfer-authorization-requests")
    public ResponseEntity<TargetApiEnvelope<
            MerchantTransferAuthorizationAcceptedView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody EmptyMerchantTransferAuthorizationRequest ignored,
            HttpServletRequest request) {
        return accepted(service.create(operationUid), request);
    }

    @PostMapping(
            "/api/v1/miniapp/me/merchant-transfer-authorization/queries")
    public ResponseEntity<TargetApiEnvelope<
            MerchantTransferAuthorizationAcceptedView>> query(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody EmptyMerchantTransferAuthorizationRequest ignored,
            HttpServletRequest request) {
        return accepted(service.requestQuery(operationUid), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> accepted(
            T data, HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
