package org.enveloping.ecobin.funds.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.funds.application.recharge.RechargeApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels.CreateRechargeRequest;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutAccountView;
import org.enveloping.ecobin.funds.web.v1.FundsModels.PayoutEntryPage;
import org.enveloping.ecobin.funds.web.v1.FundsModels.RechargePage;
import org.enveloping.ecobin.funds.web.v1.FundsModels.RechargeView;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

@RestController
public class FundsRechargeController {

    private final RechargeApplicationService service;

    public FundsRechargeController(RechargeApplicationService service) {
        this.service = service;
    }

    @PostMapping("/api/v1/web/organizations/{organizationCode}/recharge-orders")
    public ResponseEntity<TargetApiEnvelope<RechargeView>> createStaff(
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID idempotencyKey,
            @RequestBody CreateRechargeRequest body,
            HttpServletRequest request) {
        String base = "/api/v1/web/organizations/" + organizationCode;
        RechargeView view = service.create(
                false, null, organizationCode, idempotencyKey,
                body == null ? null : body.grossAmountYuan(), base);
        return accepted(view, request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/recharge-orders")
    public ResponseEntity<TargetApiEnvelope<RechargePage>> listStaff(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        String base = "/api/v1/web/organizations/" + organizationCode;
        return ok(service.list(false, null, organizationCode,
                status, limit, base), request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/recharge-orders/{rechargeNo}")
    public ResponseEntity<TargetApiEnvelope<RechargeView>> detailStaff(
            @PathVariable String organizationCode,
            @PathVariable String rechargeNo,
            HttpServletRequest request) {
        String base = "/api/v1/web/organizations/" + organizationCode;
        return ok(service.detail(false, null, organizationCode,
                rechargeNo, base), request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/payout-account")
    public ResponseEntity<TargetApiEnvelope<PayoutAccountView>> payoutStaff(
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(service.payoutAccount(false, null, organizationCode), request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/payout-account/entries")
    public ResponseEntity<TargetApiEnvelope<PayoutEntryPage>> entriesStaff(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String entryType,
            @RequestParam(required = false) String sourceNo,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.payoutEntries(false, null, organizationCode,
                entryType, sourceNo, limit), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/recharge-orders")
    public ResponseEntity<TargetApiEnvelope<RechargePage>> listPlatform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode;
        return ok(service.list(true, tenantCode, organizationCode,
                status, limit, base), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/recharge-orders/{rechargeNo}")
    public ResponseEntity<TargetApiEnvelope<RechargeView>> detailPlatform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String rechargeNo,
            HttpServletRequest request) {
        String base = "/api/v1/web/platform/tenants/" + tenantCode
                + "/organizations/" + organizationCode;
        return ok(service.detail(true, tenantCode, organizationCode,
                rechargeNo, base), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/payout-account")
    public ResponseEntity<TargetApiEnvelope<PayoutAccountView>> payoutPlatform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(service.payoutAccount(
                true, tenantCode, organizationCode), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/payout-account/entries")
    public ResponseEntity<TargetApiEnvelope<PayoutEntryPage>> entriesPlatform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String entryType,
            @RequestParam(required = false) String sourceNo,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.payoutEntries(true, tenantCode, organizationCode,
                entryType, sourceNo, limit), request);
    }

    private static ResponseEntity<TargetApiEnvelope<RechargeView>> accepted(
            RechargeView data, HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.LOCATION, data.statusUrl())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
