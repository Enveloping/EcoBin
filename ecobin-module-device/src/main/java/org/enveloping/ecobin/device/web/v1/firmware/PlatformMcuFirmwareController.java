package org.enveloping.ecobin.device.web.v1.firmware;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.firmware.McuFirmwareRolloutService;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.PageData;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RegisterReleaseRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.ReleaseView;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutActionRequest;
import org.enveloping.ecobin.device.web.v1.firmware.McuFirmwareModels.RolloutView;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.net.URI;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform")
public class PlatformMcuFirmwareController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";

    private final McuFirmwareRolloutService firmware;

    public PlatformMcuFirmwareController(
            McuFirmwareRolloutService firmware) {
        this.firmware = firmware;
    }

    @GetMapping("/mcu-firmware-releases")
    public ResponseEntity<TargetApiEnvelope<PageData<ReleaseView>>>
            listReleases(
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    HttpServletRequest request) {
        return response(firmware.listReleases(page, pageSize), request);
    }

    @PostMapping("/mcu-firmware-releases")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> registerRelease(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody RegisterReleaseRequest body,
            HttpServletRequest request) {
        ReleaseView created = firmware.registerRelease(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/mcu-firmware-releases/"
                                + created.releaseUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/mcu-firmware-releases/{releaseUid}")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> releaseDetail(
            @PathVariable UUID releaseUid,
            HttpServletRequest request) {
        return response(firmware.releaseDetail(releaseUid), request);
    }

    @GetMapping("/mcu-firmware-rollouts")
    public ResponseEntity<TargetApiEnvelope<PageData<RolloutView>>>
            listRollouts(
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    HttpServletRequest request) {
        return response(firmware.listRollouts(page, pageSize), request);
    }

    @PostMapping("/mcu-firmware-rollouts")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> createRollout(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateRolloutRequest body,
            HttpServletRequest request) {
        RolloutView created = firmware.createRollout(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/mcu-firmware-rollouts/"
                                + created.rolloutUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/mcu-firmware-rollouts/{rolloutUid}")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> detail(
            @PathVariable UUID rolloutUid,
            HttpServletRequest request) {
        return response(firmware.detail(rolloutUid), request);
    }

    @PostMapping(
            "/mcu-firmware-rollouts/{rolloutUid}/validation-starts")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> startValidation(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID rolloutUid,
            @Valid @RequestBody RolloutActionRequest body,
            HttpServletRequest request) {
        return response(firmware.startValidation(
                operationUid, rolloutUid, body), request);
    }

    @PostMapping("/mcu-firmware-rollouts/{rolloutUid}/promotions")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> promote(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID rolloutUid,
            @Valid @RequestBody RolloutActionRequest body,
            HttpServletRequest request) {
        return response(firmware.promote(
                operationUid, rolloutUid, body), request);
    }

    @PostMapping(
            "/mcu-firmware-rollouts/{rolloutUid}/wave-advancements")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> advanceWave(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID rolloutUid,
            @Valid @RequestBody RolloutActionRequest body,
            HttpServletRequest request) {
        return response(firmware.advanceWave(
                operationUid, rolloutUid, body), request);
    }

    @PostMapping("/mcu-firmware-rollouts/{rolloutUid}/stoppages")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> stop(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID rolloutUid,
            @Valid @RequestBody RolloutActionRequest body,
            HttpServletRequest request) {
        return response(firmware.stop(
                operationUid, rolloutUid, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> response(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(ok(data, request));
    }

    private static <T> TargetApiEnvelope<T> ok(
            T data,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(data, TargetRequestIds.resolve(request));
    }
}
