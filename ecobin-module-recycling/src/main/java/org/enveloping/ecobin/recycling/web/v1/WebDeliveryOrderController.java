package org.enveloping.ecobin.recycling.web.v1;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.framework.web.v1.TargetApiEnvelope;
import org.enveloping.ecobin.framework.web.v1.TargetRequestIds;
import org.enveloping.ecobin.recycling.application.deliveryorder.DeliveryOrderQueryService;
import org.enveloping.ecobin.recycling.application.deliveryorder.DeliveryOrderReviewService;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.CursorPage;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewPreview;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.DeliveryReviewResult;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.PreviewDeliveryReviewRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.ReviewDeliveryOrderRequest;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryOrderDetail;
import org.enveloping.ecobin.recycling.web.v1.DeliveryOrderModels.WebDeliveryOrderItem;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.http.HttpStatus;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.UUID;

@RestController
public class WebDeliveryOrderController {

    private final DeliveryOrderQueryService queryService;
    private final DeliveryOrderReviewService reviewService;

    public WebDeliveryOrderController(
            DeliveryOrderQueryService queryService,
            DeliveryOrderReviewService reviewService) {
        this.queryService = queryService;
        this.reviewService = reviewService;
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-orders")
    public TargetApiEnvelope<CursorPage<WebDeliveryOrderItem>>
    staffOrders(
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String reviewStatus,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false)
            UUID organizationUserUid,
            @RequestParam(required = false) String deviceCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false) String anomalyCode,
            @RequestParam(required = false) String photoCompleteness,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOrders(
                        false,
                        null,
                        organizationCode,
                        cursor,
                        limit,
                        reviewStatus,
                        occurredFrom,
                        occurredTo,
                        organizationUserUid,
                        deviceCode,
                        portNo,
                        anomalyCode,
                        photoCompleteness),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-orders")
    public TargetApiEnvelope<CursorPage<WebDeliveryOrderItem>>
    platformOrders(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false) Integer limit,
            @RequestParam(required = false) String reviewStatus,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredFrom,
            @RequestParam(required = false)
            @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME)
            Instant occurredTo,
            @RequestParam(required = false)
            UUID organizationUserUid,
            @RequestParam(required = false) String deviceCode,
            @RequestParam(required = false) Integer portNo,
            @RequestParam(required = false) String anomalyCode,
            @RequestParam(required = false) String photoCompleteness,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOrders(
                        true,
                        tenantCode,
                        organizationCode,
                        cursor,
                        limit,
                        reviewStatus,
                        occurredFrom,
                        occurredTo,
                        organizationUserUid,
                        deviceCode,
                        portNo,
                        anomalyCode,
                        photoCompleteness),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}")
    public TargetApiEnvelope<WebDeliveryOrderDetail> staffOrder(
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOrder(
                        false,
                        null,
                        organizationCode,
                        deliveryOrderNo),
                TargetRequestIds.resolve(request));
    }

    @GetMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}")
    public TargetApiEnvelope<WebDeliveryOrderDetail> platformOrder(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            HttpServletRequest request) {
        return TargetApiEnvelope.ok(
                queryService.webOrder(
                        true,
                        tenantCode,
                        organizationCode,
                        deliveryOrderNo),
                TargetRequestIds.resolve(request));
    }

    @PostMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/review-previews")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewPreview>>
    staffReviewPreview(
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @Valid @RequestBody PreviewDeliveryReviewRequest body,
            HttpServletRequest request) {
        return previewed(
                reviewService.preview(
                        false,
                        null,
                        organizationCode,
                        deliveryOrderNo,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/review-previews")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewPreview>>
    platformReviewPreview(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @Valid @RequestBody PreviewDeliveryReviewRequest body,
            HttpServletRequest request) {
        return previewed(
                reviewService.preview(
                        true,
                        tenantCode,
                        organizationCode,
                        deliveryOrderNo,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/reviews")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewResult>>
    staffReview(
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody ReviewDeliveryOrderRequest body,
            HttpServletRequest request) {
        return created(
                reviewService.review(
                        false,
                        null,
                        organizationCode,
                        deliveryOrderNo,
                        operationUid,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/reviews")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewResult>>
    platformReview(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody ReviewDeliveryOrderRequest body,
            HttpServletRequest request) {
        return created(
                reviewService.review(
                        true,
                        tenantCode,
                        organizationCode,
                        deliveryOrderNo,
                        operationUid,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/corrections")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewResult>>
    staffCorrection(
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody ReviewDeliveryOrderRequest body,
            HttpServletRequest request) {
        return created(
                reviewService.correct(
                        false,
                        null,
                        organizationCode,
                        deliveryOrderNo,
                        operationUid,
                        body),
                request);
    }

    @PostMapping(
            "/api/v1/web/platform/tenants/{tenantCode}"
                    + "/organizations/{organizationCode}"
                    + "/delivery-orders/{deliveryOrderNo}/corrections")
    public ResponseEntity<TargetApiEnvelope<DeliveryReviewResult>>
    platformCorrection(
            @PathVariable String tenantCode,
            @PathVariable String organizationCode,
            @PathVariable String deliveryOrderNo,
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @Valid @RequestBody ReviewDeliveryOrderRequest body,
            HttpServletRequest request) {
        return created(
                reviewService.correct(
                        true,
                        tenantCode,
                        organizationCode,
                        deliveryOrderNo,
                        operationUid,
                        body),
                request);
    }

    private static ResponseEntity<TargetApiEnvelope<DeliveryReviewResult>>
    created(
            DeliveryReviewResult result,
            HttpServletRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(TargetApiEnvelope.ok(
                        result,
                        TargetRequestIds.resolve(request)));
    }

    private static ResponseEntity<TargetApiEnvelope<DeliveryReviewPreview>>
    previewed(
            DeliveryReviewPreview result,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        result,
                        TargetRequestIds.resolve(request)));
    }
}
