package org.enveloping.ecobin.funds.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.funds.application.binding.MerchantBindingApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels.DisableMerchantBindingRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels.MerchantBindingView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.VerifyMerchantBindingRequest;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wechat-merchant-binding")
public class MerchantBindingController {

    private final MerchantBindingApplicationService service;

    public MerchantBindingController(MerchantBindingApplicationService service) {
        this.service = service;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<MerchantBindingView>> get(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(service.get(tenantCode, organizationCode), request);
    }

    @PostMapping("/verifications")
    public ResponseEntity<TargetApiEnvelope<MerchantBindingView>> verify(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody VerifyMerchantBindingRequest body,
            HttpServletRequest request) {
        return ok(service.verify(
                tenantCode, organizationCode, operationUid, body), request);
    }

    @PostMapping("/disablements")
    public ResponseEntity<TargetApiEnvelope<MerchantBindingView>> disable(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody DisableMerchantBindingRequest body,
            HttpServletRequest request) {
        return ok(service.disable(
                tenantCode, organizationCode, operationUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
