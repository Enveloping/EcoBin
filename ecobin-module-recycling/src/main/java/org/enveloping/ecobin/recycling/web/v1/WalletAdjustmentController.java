package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.walletadjustment.WalletAdjustmentApplicationService;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.AdjustWalletRequest;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.WalletAdjustmentView;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
public class WalletAdjustmentController {

    private final WalletAdjustmentApplicationService service;

    public WalletAdjustmentController(
            WalletAdjustmentApplicationService service) {
        this.service = service;
    }

    @PostMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}"
                    + "/wallet-adjustments")
    public ResponseEntity<TargetApiEnvelope<WalletAdjustmentView>> staff(
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody AdjustWalletRequest body,
            HttpServletRequest request) {
        return created(service.adjust(
                false, null, organizationCode,
                organizationUserUid, operationUid, body), request);
    }

    @PostMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}"
                    + "/wallet-adjustments")
    public ResponseEntity<TargetApiEnvelope<WalletAdjustmentView>> platform(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @RequestBody AdjustWalletRequest body,
            HttpServletRequest request) {
        return created(service.adjust(
                true, tenantCode, organizationCode,
                organizationUserUid, operationUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> created(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
