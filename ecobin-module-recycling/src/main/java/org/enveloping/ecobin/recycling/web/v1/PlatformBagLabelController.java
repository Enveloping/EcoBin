package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.bag.PlatformBagLabelApplicationService;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.BagLabelBatchSummary;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.BagLabelBatchView;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.CreateBagLabelBatchRequest;
import org.enveloping.ecobin.recycling.web.v1.BagLabelModels.PageData;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
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
@RequestMapping("/api/v1/web/platform/bag-label-batches")
public class PlatformBagLabelController {

    private final PlatformBagLabelApplicationService application;

    public PlatformBagLabelController(
            PlatformBagLabelApplicationService application) {
        this.application = application;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<PageData<BagLabelBatchSummary>>>
            list(
                    @RequestParam(defaultValue = "1") int page,
                    @RequestParam(defaultValue = "20") int pageSize,
                    HttpServletRequest request) {
        return noStore(application.list(page, pageSize), request);
    }

    @PostMapping
    public ResponseEntity<TargetApiEnvelope<BagLabelBatchView>> create(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody CreateBagLabelBatchRequest body,
            HttpServletRequest request) {
        BagLabelBatchView created = application.create(operationUid, body);
        return ResponseEntity.created(URI.create(
                        "/api/v1/web/platform/bag-label-batches/"
                                + created.batchUid()))
                .cacheControl(CacheControl.noStore())
                .body(ok(created, request));
    }

    @GetMapping("/{batchUid}")
    public ResponseEntity<TargetApiEnvelope<BagLabelBatchView>> detail(
            @PathVariable UUID batchUid,
            HttpServletRequest request) {
        return noStore(application.detail(batchUid), request);
    }

    @DeleteMapping("/{batchUid}")
    public ResponseEntity<Void> delete(@PathVariable UUID batchUid) {
        application.delete(batchUid);
        return ResponseEntity.noContent()
                .cacheControl(CacheControl.noStore())
                .build();
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
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
