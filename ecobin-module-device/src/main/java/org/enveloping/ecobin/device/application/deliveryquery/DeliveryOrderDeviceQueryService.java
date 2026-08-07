package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFactsRef;
import org.enveloping.ecobin.device.api.persistence.DeliveryOrderDeviceFilterRef;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFactsQueryPort;
import org.enveloping.ecobin.device.api.port.DeliveryOrderDeviceFilterQueryPort;
import org.enveloping.ecobin.device.api.query.DeliveryOrderDeviceFilterQuery;
import org.enveloping.ecobin.device.api.result.DeliveryOrderDeviceFacts;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;

@Service
public class DeliveryOrderDeviceQueryService
        implements DeliveryOrderDeviceFactsQueryPort,
        DeliveryOrderDeviceFilterQueryPort {

    private static final Pattern DEVICE_CODE = Pattern.compile(
            "Dv_[A-Za-z0-9_-]{24,61}");

    private final DeliveryOrderDeviceQueryRepository repository;
    private final DeliveryReadQueryRefFactory queryRefFactory;

    DeliveryOrderDeviceQueryService(
            DeliveryOrderDeviceQueryRepository repository,
            DeliveryReadQueryRefFactory queryRefFactory) {
        this.repository = repository;
        this.queryRefFactory = queryRefFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public List<DeliveryOrderDeviceFacts> facts(
            DeliveryOrderDeviceFactsRef factsRef) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(factsRef, "factsRef");
        var callbackClaimed = new AtomicBoolean();
        var computed = new AtomicReference<
                List<DeliveryOrderDeviceFacts>>();
        factsRef.withFactKeysOnce(batch -> {
            claimCallbackOnce(callbackClaimed);
            computed.set(factsWithinScope(
                    Objects.requireNonNull(
                            batch,
                            "batch")));
            return null;
        });
        return List.copyOf(
                Objects.requireNonNull(
                        computed.get(),
                        "factsRef did not supply fact keys"));
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public Optional<DeliveryOrderDeviceFilterRef> resolveFilter(
            DeliveryOrderDeviceFilterQuery query) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(query, "query");
        var callbackClaimed = new AtomicBoolean();
        var computed = new AtomicReference<
                Optional<DeliveryOrderDeviceFilterRef>>();
        query.scopeRef().withScopeOnce(
                (tenantId,
                 organizationId,
                 platformAdminId,
                 staffAccountId) -> {
                    claimCallbackOnce(callbackClaimed);
                    computed.set(filterWithinScope(
                            tenantId,
                            organizationId,
                            query.deviceCode(),
                            query.portNo()));
                    return null;
                });
        return Objects.requireNonNull(
                computed.get(),
                "scopeRef did not supply a scope");
    }

    private List<DeliveryOrderDeviceFacts> factsWithinScope(
            DeliveryOrderDeviceFactsRef.BatchKeys batch) {
        List<DeliveryOrderDeviceFactsRef.FactKey> requested =
                batch.facts();
        List<DeliveryOrderDeviceQueryRepository.ResolvedFactRow>
                rows = repository.findFacts(
                batch.tenantKey(),
                batch.organizationKey(),
                requested);
        Map<FactTuple,
                DeliveryOrderDeviceQueryRepository.ResolvedFactRow>
                resolved = new HashMap<>();
        for (DeliveryOrderDeviceQueryRepository.ResolvedFactRow row
                : rows) {
            var tuple = tuple(row);
            if (resolved.put(tuple, row) != null) {
                throw new IllegalStateException(
                        "device fact query returned an ambiguous tuple");
            }
        }
        return requested.stream()
                .map(fact -> resolvedFact(
                        fact,
                        resolved.get(tuple(fact))))
                .toList();
    }

    private Optional<DeliveryOrderDeviceFilterRef> filterWithinScope(
            long tenantId,
            long organizationId,
            String deviceCode,
            Integer portNo) {
        if (deviceCode == null) {
            List<Long> portKeys = repository.findPortFilterKeys(
                    tenantId,
                    organizationId,
                    Objects.requireNonNull(portNo, "portNo"));
            if (portKeys.isEmpty()) {
                return Optional.empty();
            }
            return Optional.of(queryRefFactory.issueOrderFilter(
                    tenantId,
                    organizationId,
                    null,
                    portKeys));
        }
        if (!DEVICE_CODE.matcher(deviceCode).matches()) {
            return Optional.empty();
        }
        return repository.resolveAssetFilter(
                        tenantId,
                        organizationId,
                        deviceCode,
                        portNo)
                .filter(row -> portNo == null || row.portId() != null)
                .map(row -> queryRefFactory.issueOrderFilter(
                        tenantId,
                        organizationId,
                        row.assetId(),
                        row.portId() == null
                                ? List.of()
                                : List.of(row.portId())));
    }

    private static DeliveryOrderDeviceFacts resolvedFact(
            DeliveryOrderDeviceFactsRef.FactKey requested,
            DeliveryOrderDeviceQueryRepository.ResolvedFactRow row) {
        if (row == null) {
            return DeliveryOrderDeviceFacts.missing(
                    requested.token());
        }
        return new DeliveryOrderDeviceFacts(
                requested.token(),
                row.eventUid(),
                row.sessionUid(),
                row.deviceCode(),
                row.portNo());
    }

    private static FactTuple tuple(
            DeliveryOrderDeviceFactsRef.FactKey fact) {
        return new FactTuple(
                fact.assetKey(),
                fact.portKey(),
                fact.deliverySessionKey(),
                fact.physicalResultKey());
    }

    private static FactTuple tuple(
            DeliveryOrderDeviceQueryRepository.ResolvedFactRow row) {
        return new FactTuple(
                row.assetId(),
                row.portId(),
                row.deliverySessionId(),
                row.physicalResultId());
    }

    private static void claimCallbackOnce(
            AtomicBoolean callbackClaimed) {
        if (!callbackClaimed.compareAndSet(false, true)) {
            throw new IllegalStateException(
                    "query reference invoked its callback more than once");
        }
    }

    private static void requireReadOnlyTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "delivery order device query requires an existing "
                            + "read-only transaction");
        }
    }

    private record FactTuple(
            long assetId,
            long portId,
            long deliverySessionId,
            long physicalResultId) {
    }
}
