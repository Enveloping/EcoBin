package org.enveloping.ecobin.device.application.deliveryquery;

import org.enveloping.ecobin.device.api.persistence.DeliveryOptionsBusinessQueryRef;
import org.enveloping.ecobin.device.api.port.MiniappDeliveryDeviceQueryPort;
import org.enveloping.ecobin.device.api.query.DeliveryDeviceOptionsQuery;
import org.enveloping.ecobin.device.api.query.OwnedDeliverySessionQuery;
import org.enveloping.ecobin.device.api.result.DeliveryDeviceOptionsSnapshot;
import org.enveloping.ecobin.device.api.result.DeliveryDevicePortOptionSnapshot;
import org.enveloping.ecobin.device.api.result.OwnedDeliverySessionSnapshot;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Objects;

@Service
public class MiniappDeliveryDeviceQueryService
        implements MiniappDeliveryDeviceQueryPort {

    private final MiniappDeliveryDeviceQueryRepository repository;
    private final DeliveryReadQueryRefFactory queryRefFactory;

    MiniappDeliveryDeviceQueryService(
            MiniappDeliveryDeviceQueryRepository repository,
            DeliveryReadQueryRefFactory queryRefFactory) {
        this.repository = repository;
        this.queryRefFactory = queryRefFactory;
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public DeliveryDeviceOptionsSnapshot deliveryOptions(
            DeliveryDeviceOptionsQuery query) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(query, "query");
        String deviceCode =
                normalizeDeviceCode(query.deviceCode());
        return query.organizationUserRef()
                .withDeliveryQueryUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId,
                         organizationUserUid) -> optionsWithinScope(
                                tenantId,
                                organizationId,
                                deviceCode));
    }

    @Override
    @Transactional(
            propagation = Propagation.MANDATORY,
            readOnly = true)
    public OwnedDeliverySessionSnapshot ownedSession(
            OwnedDeliverySessionQuery query) {
        requireReadOnlyTransaction();
        Objects.requireNonNull(query, "query");
        return query.organizationUserRef()
                .withDeliveryQueryUserOnce(
                        (tenantId,
                         organizationId,
                         organizationUserId,
                         organizationUserUid) -> repository
                                .findOwnedSession(
                                        tenantId,
                                        organizationId,
                                        organizationUserId,
                                        query.sessionUid())
                                .map(row -> ownedSession(
                                        row,
                                        tenantId,
                                        organizationId))
                                .orElseThrow(
                                        MiniappDeliveryDeviceQueryService
                                                ::notFound));
    }

    private DeliveryDeviceOptionsSnapshot optionsWithinScope(
            long tenantId,
            long organizationId,
            String deviceCode) {
        MiniappDeliveryDeviceQueryRepository.AssetSnapshotRow
                asset = repository.findAsset(
                                tenantId,
                                organizationId,
                                deviceCode)
                        .orElseThrow(
                                MiniappDeliveryDeviceQueryService
                                        ::notFound);
        List<MiniappDeliveryDeviceQueryRepository.PortSnapshotRow>
                portRows = repository.findPorts(
                tenantId,
                organizationId,
                asset.assetId(),
                asset.configurationId());
        LocalDateTime now = repository.databaseNow();
        MiniappDeliveryDeviceQueryPolicy.Evaluation evaluation =
                MiniappDeliveryDeviceQueryPolicy.evaluate(
                        asset,
                        portRows,
                        now);

        List<DeliveryDevicePortOptionSnapshot> ports =
                evaluation.ports().stream()
                        .map(port ->
                                new DeliveryDevicePortOptionSnapshot(
                                        port.portNo(),
                                        port.displayName(),
                                        port.unitPriceYuanPerKg(),
                                        port.fullnessMode(),
                                        port.blockers()))
                        .toList();
        List<DeliveryOptionsBusinessQueryRef.PortKey> businessPorts =
                evaluation.ports().stream()
                        .map(port ->
                                new DeliveryOptionsBusinessQueryRef
                                        .PortKey(
                                        port.portId(),
                                        port.portNo()))
                        .toList();
        return new DeliveryDeviceOptionsSnapshot(
                asset.deviceCode(),
                evaluation.exactConfiguration()
                        ? asset.deviceDisplayName()
                        : null,
                evaluation.exactConfiguration()
                        ? asset.locationAddress()
                        : null,
                asset.deviceBusy(),
                now.toInstant(ZoneOffset.UTC),
                ports,
                queryRefFactory.issueOptionsBusiness(
                        tenantId,
                        organizationId,
                        asset.assetId(),
                        businessPorts));
    }

    private OwnedDeliverySessionSnapshot ownedSession(
            MiniappDeliveryDeviceQueryRepository.OwnedSessionRow row,
            long tenantId,
            long organizationId) {
        return new OwnedDeliverySessionSnapshot(
                row.sessionUid(),
                row.deviceStatus(),
                row.deviceCode(),
                row.portNo(),
                instant(row.firstPhysicalProgressAt()),
                instant(row.deviceCompletedAt()),
                instant(row.endedAt()),
                row.endReason(),
                instant(row.offlineOccupancyReleasedAt()),
                queryRefFactory.issueSessionBusiness(
                        tenantId,
                        organizationId,
                        row.sessionId()));
    }

    private static java.time.Instant instant(LocalDateTime value) {
        return value == null
                ? null
                : value.toInstant(ZoneOffset.UTC);
    }

    private static String normalizeDeviceCode(String value) {
        String normalized = value.trim();
        if (!normalized.matches("Dv_[A-Za-z0-9_-]{24,61}")) {
            throw notFound();
        }
        return normalized;
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "RESOURCE.NOT_FOUND",
                "请求的资源不存在");
    }

    private static void requireReadOnlyTransaction() {
        if (!TransactionSynchronizationManager
                .isActualTransactionActive()
                || !TransactionSynchronizationManager
                .isCurrentTransactionReadOnly()
                || !TransactionSynchronizationManager
                .isSynchronizationActive()) {
            throw new IllegalStateException(
                    "miniapp delivery device query requires an "
                            + "existing read-only transaction");
        }
    }
}
