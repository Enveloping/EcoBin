package org.enveloping.ecobin.device.web.v1.factory;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.enveloping.ecobin.device.application.factory.FactoryAcceptanceService;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.CorrectFactoryBagRequest;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.FactoryAcceptanceView;
import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.InstallFactoryBagRequest;
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
import org.springframework.web.bind.annotation.RestController;

import java.util.UUID;

@RestController
@RequestMapping("/api/v1/miniapp-factory/device-assets/{deviceCode}")
public class FactoryAcceptanceController {

    private final FactoryAcceptanceService acceptance;

    public FactoryAcceptanceController(
            FactoryAcceptanceService acceptance) {
        this.acceptance = acceptance;
    }

    @GetMapping
    public ResponseEntity<TargetApiEnvelope<FactoryAcceptanceView>> detail(
            @PathVariable String deviceCode,
            HttpServletRequest request) {
        return noStore(acceptance.detail(deviceCode), request);
    }

    @PostMapping("/factory-bags")
    public ResponseEntity<TargetApiEnvelope<FactoryAcceptanceView>> install(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String deviceCode,
            @Valid @RequestBody InstallFactoryBagRequest body,
            HttpServletRequest request) {
        return noStore(acceptance.install(
                operationUid, deviceCode, body), request);
    }

    @PostMapping("/factory-bags/{portNo}/corrections")
    public ResponseEntity<TargetApiEnvelope<FactoryAcceptanceView>> correct(
            @RequestHeader("Idempotency-Key") UUID operationUid,
            @PathVariable String deviceCode,
            @PathVariable int portNo,
            @Valid @RequestBody CorrectFactoryBagRequest body,
            HttpServletRequest request) {
        return noStore(acceptance.correct(
                operationUid, deviceCode, portNo, body), request);
    }

    private static <T> ResponseEntity<TargetApiEnvelope<T>> noStore(
            T data,
            HttpServletRequest request) {
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(TargetApiEnvelope.ok(
                        data, TargetRequestIds.resolve(request)));
    }
}
