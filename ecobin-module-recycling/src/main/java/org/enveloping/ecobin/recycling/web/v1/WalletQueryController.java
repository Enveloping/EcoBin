package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.walletquery.WalletReadApplicationService;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.MiniappWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.OrganizationWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.PersonalWalletEntry;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.WalletEntryPage;
import org.enveloping.ecobin.recycling.web.v1.WalletModels.WalletSummary;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class WalletQueryController {

    private final WalletReadApplicationService service;

    public WalletQueryController(
            WalletReadApplicationService service) {
        this.service = service;
    }

    @GetMapping("/api/v1/miniapp/me/wallet")
    public ResponseEntity<TargetApiEnvelope<WalletSummary>>
    miniappWallet(HttpServletRequest request) {
        return noStore(service.miniappSummary(), request);
    }

    @GetMapping("/api/v1/miniapp/me/wallet/entries")
    public ResponseEntity<TargetApiEnvelope<
            WalletEntryPage<MiniappWalletEntry>>>
    miniappEntries(
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.miniappEntries(cursor, limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}/wallet")
    public ResponseEntity<TargetApiEnvelope<WalletSummary>>
    staffWallet(
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            HttpServletRequest request) {
        return noStore(
                service.webSummary(
                        false,
                        null,
                        organizationCode,
                        organizationUserUid),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}/wallet")
    public ResponseEntity<TargetApiEnvelope<WalletSummary>>
    platformWallet(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            HttpServletRequest request) {
        return noStore(
                service.webSummary(
                        true,
                        tenantCode,
                        organizationCode,
                        organizationUserUid),
                request);
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}"
                    + "/wallet/entries")
    public ResponseEntity<TargetApiEnvelope<
            WalletEntryPage<PersonalWalletEntry>>>
    staffPersonalEntries(
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webPersonalEntries(
                        false,
                        null,
                        organizationCode,
                        organizationUserUid,
                        cursor,
                        limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/organization-users/{organizationUserUid}"
                    + "/wallet/entries")
    public ResponseEntity<TargetApiEnvelope<
            WalletEntryPage<PersonalWalletEntry>>>
    platformPersonalEntries(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable UUID organizationUserUid,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webPersonalEntries(
                        true,
                        tenantCode,
                        organizationCode,
                        organizationUserUid,
                        cursor,
                        limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/wallet-entries")
    public ResponseEntity<TargetApiEnvelope<
            WalletEntryPage<OrganizationWalletEntry>>>
    staffOrganizationEntries(
            @PathVariable String organizationCode,
            @RequestParam(required = false)
            UUID organizationUserUid,
            @RequestParam(required = false) String entryType,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false) String sourceNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webOrganizationEntries(
                        false,
                        null,
                        organizationCode,
                        organizationUserUid,
                        entryType,
                        occurredFrom,
                        occurredTo,
                        sourceNo,
                        cursor,
                        limit),
                request);
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/wallet-entries")
    public ResponseEntity<TargetApiEnvelope<
            WalletEntryPage<OrganizationWalletEntry>>>
    platformOrganizationEntries(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false)
            UUID organizationUserUid,
            @RequestParam(required = false) String entryType,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false) String sourceNo,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            HttpServletRequest request) {
        return noStore(
                service.webOrganizationEntries(
                        true,
                        tenantCode,
                        organizationCode,
                        organizationUserUid,
                        entryType,
                        occurredFrom,
                        occurredTo,
                        sourceNo,
                        cursor,
                        limit),
                request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data,
                        TargetRequestIds.resolve(request)));
    }
}
