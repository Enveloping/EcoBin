package org.enveloping.ecobin.funds.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.funds.application.withdrawal.WithdrawalApplicationService;
import org.enveloping.ecobin.funds.web.v1.FundsModels.*;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

@RestController
public class WithdrawalController {

    private final WithdrawalApplicationService service;

    public WithdrawalController(WithdrawalApplicationService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/withdrawal-configuration")
    public ResponseEntity<TargetApiEnvelope<WithdrawalConfigurationView>>
    staffConfiguration(
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(service.configuration(
                false, null, organizationCode), request);
    }

    @PostMapping("/api/v1/web/organizations/{organizationCode}/withdrawal-configuration-releases")
    public ResponseEntity<TargetApiEnvelope<WithdrawalConfigurationView>>
    releaseStaffConfiguration(
            @PathVariable String organizationCode,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody ReleaseWithdrawalConfigurationRequest body,
            HttpServletRequest request) {
        return created(service.releaseConfiguration(
                false, null, organizationCode, operationUid, body), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/withdrawal-configuration")
    public ResponseEntity<TargetApiEnvelope<WithdrawalConfigurationView>>
    platformConfiguration(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            HttpServletRequest request) {
        return ok(service.configuration(
                true, tenantCode, organizationCode), request);
    }

    @GetMapping("/api/v1/web/platform/payout-gate")
    public ResponseEntity<TargetApiEnvelope<PayoutGateView>> payoutGate(
            HttpServletRequest request) {
        return ok(service.payoutGate(), request);
    }

    @PostMapping("/api/v1/web/platform/payout-gate/restorations")
    public ResponseEntity<TargetApiEnvelope<PayoutGateView>> restorePayoutGate(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody RestorePayoutGateRequest body,
            HttpServletRequest request) {
        return ok(service.restorePayoutGate(operationUid, body), request);
    }

    @PostMapping("/api/v1/miniapp/me/withdrawals")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody CreateWithdrawalRequest body,
            HttpServletRequest request) {
        return created(service.create(operationUid, body), request);
    }

    @GetMapping("/api/v1/miniapp/me/withdrawals")
    public ResponseEntity<TargetApiEnvelope<WithdrawalPage>> miniappList(
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.miniappList(status, limit), request);
    }

    @GetMapping("/api/v1/miniapp/me/withdrawals/{withdrawalNo}")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> miniappDetail(
            @PathVariable String withdrawalNo,
            HttpServletRequest request) {
        return ok(service.miniappDetail(withdrawalNo), request);
    }

    @GetMapping("/api/v1/miniapp/me/withdrawals/{withdrawalNo}/merchant-transfer-confirmation")
    public ResponseEntity<TargetApiEnvelope<MerchantTransferConfirmationView>>
    confirmation(
            @PathVariable String withdrawalNo,
            HttpServletRequest request) {
        return ok(service.confirmation(withdrawalNo), request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/withdrawals")
    public ResponseEntity<TargetApiEnvelope<WithdrawalPage>> staffList(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.webList(false, null, organizationCode,
                status, limit), request);
    }

    @GetMapping("/api/v1/web/organizations/{organizationCode}/withdrawals/{withdrawalNo}")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> staffDetail(
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            HttpServletRequest request) {
        return ok(service.webDetail(false, null, organizationCode,
                withdrawalNo), request);
    }

    @PostMapping("/api/v1/web/organizations/{organizationCode}/withdrawals/{withdrawalNo}/reviews")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> reviewStaff(
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody ReviewWithdrawalRequest body,
            HttpServletRequest request) {
        return ok(service.review(false, null, organizationCode,
                withdrawalNo, operationUid, body), request);
    }

    @PostMapping("/api/v1/web/organizations/{organizationCode}/withdrawals/{withdrawalNo}/pre-channel-terminations")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> abortStaff(
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody VersionedWithdrawalRequest body,
            HttpServletRequest request) {
        return ok(service.abortBeforeChannel(false, null, organizationCode,
                withdrawalNo, operationUid, body), request);
    }

    @PostMapping("/api/v1/web/organizations/{organizationCode}/withdrawals/{withdrawalNo}/channel-queries")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> queryStaff(
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody VersionedWithdrawalRequest body,
            HttpServletRequest request) {
        return accepted(service.requestChannelAction(
                false, null, organizationCode, withdrawalNo,
                operationUid, body), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/withdrawals")
    public ResponseEntity<TargetApiEnvelope<WithdrawalPage>> platformList(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return ok(service.webList(true, tenantCode, organizationCode,
                status, limit), request);
    }

    @GetMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/withdrawals/{withdrawalNo}")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> platformDetail(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            HttpServletRequest request) {
        return ok(service.webDetail(true, tenantCode, organizationCode,
                withdrawalNo), request);
    }

    @PostMapping("/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/withdrawals/{withdrawalNo}/reviews")
    public ResponseEntity<TargetApiEnvelope<WithdrawalView>> reviewPlatform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String withdrawalNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody ReviewWithdrawalRequest body,
            HttpServletRequest request) {
        return ok(service.review(true, tenantCode, organizationCode,
                withdrawalNo, operationUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> ok(
            T data, HttpServletRequest request) {
        return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> created(
            T data, HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> accepted(
            T data, HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
