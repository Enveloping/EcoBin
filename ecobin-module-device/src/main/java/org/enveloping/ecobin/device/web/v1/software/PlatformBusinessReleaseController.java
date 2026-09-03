package org.enveloping.ecobin.device.web.v1.software;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.software.BusinessReleaseControlPlaneService;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ControlPlaneReadinessView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateReleaseDraftRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.CreateRolloutRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.PageData;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ReleaseActionRequest;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.ReleaseView;
import org.enveloping.ecobin.device.web.v1.software.BusinessReleaseModels.RolloutView;
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
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;

@RestController
@RequestMapping("/api/v1/web/platform/business-releases")
public class PlatformBusinessReleaseController {

    private static final String IDEMPOTENCY_KEY = "Idempotency-Key";
    private final BusinessReleaseControlPlaneService releases;

    public PlatformBusinessReleaseController(
            BusinessReleaseControlPlaneService releases) {
        this.releases = releases;
    }

    @GetMapping("/readiness")
    public ResponseEntity<TargetApiEnvelope<ControlPlaneReadinessView>> readiness(
            HttpServletRequest request) {
        return response(releases.readiness(), request);
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<ReleaseView>>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            HttpServletRequest request) {
        return response(releases.listReleases(page, pageSize), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> create(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateReleaseDraftRequest body,
            HttpServletRequest request) {
        ReleaseView created = releases.createDraft(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/business-releases/"
                                + created.releaseUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/{releaseUid}")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> detail(
            @PathVariable UUID releaseUid,
            HttpServletRequest request) {
        return response(releases.releaseDetail(releaseUid), request);
    }

    @PostMapping(path = "/{releaseUid}/artifacts")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> upload(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @RequestPart("package") MultipartFile packageFile,
            @RequestPart("signature") MultipartFile signatureFile,
            @RequestParam String signingKeyId,
            @RequestParam String reason,
            HttpServletRequest request) throws IOException {
        Path directory = Files.createTempDirectory("ecobin-release-upload-");
        Path packagePath = directory.resolve("package.tar.gz");
        Path signaturePath = directory.resolve("package.sig");
        try {
            packageFile.transferTo(packagePath);
            signatureFile.transferTo(signaturePath);
            return response(releases.uploadArtifacts(
                    operationUid,
                    releaseUid,
                    packagePath,
                    signaturePath,
                    signingKeyId,
                    reason), request);
        } finally {
            deleteTemporaryUpload(packagePath, signaturePath, directory);
        }
    }

    @PostMapping("/{releaseUid}/verifications")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> verify(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.verify(
                operationUid, releaseUid, body.reason()), request);
    }

    @PostMapping("/{releaseUid}/approvals")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> approve(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.approve(
                operationUid, releaseUid, body.reason()), request);
    }

    @PostMapping("/{releaseUid}/suspensions")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> suspend(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.suspend(
                operationUid, releaseUid, body.reason()), request);
    }

    @PostMapping("/{releaseUid}/resumptions")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> resume(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.resume(
                operationUid, releaseUid, body.reason()), request);
    }

    @PostMapping("/{releaseUid}/retirements")
    public ResponseEntity<TargetApiEnvelope<ReleaseView>> retire(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID releaseUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.retire(
                operationUid, releaseUid, body.reason()), request);
    }

    @GetMapping("/rollouts")
    public ResponseEntity<TargetApiEnvelope<PageData<RolloutView>>> rollouts(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "20") int pageSize,
            HttpServletRequest request) {
        return response(releases.listRollouts(page, pageSize), request);
    }

    @PostMapping("/rollouts")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> createRollout(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @Valid @RequestBody CreateRolloutRequest body,
            HttpServletRequest request) {
        RolloutView created = releases.createRollout(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/business-releases/rollouts/"
                                + created.rolloutUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/rollouts/{rolloutUid}")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> rolloutDetail(
            @PathVariable UUID rolloutUid,
            HttpServletRequest request) {
        return response(releases.rolloutDetail(rolloutUid), request);
    }

    @PostMapping("/rollouts/{rolloutUid}/stoppages")
    public ResponseEntity<TargetApiEnvelope<RolloutView>> stopRollout(
            @RequestHeader(IDEMPOTENCY_KEY) UUID operationUid,
            @PathVariable UUID rolloutUid,
            @Valid @RequestBody ReleaseActionRequest body,
            HttpServletRequest request) {
        return response(releases.stopRollout(
                operationUid, rolloutUid, body.reason()), request);
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

    private static void deleteTemporaryUpload(Path... paths) {
        for (Path path : paths) {
            try {
                Files.deleteIfExists(path);
            } catch (IOException ignored) {
                // Cleanup failure must not disguise an already persisted result.
            }
        }
    }
}
