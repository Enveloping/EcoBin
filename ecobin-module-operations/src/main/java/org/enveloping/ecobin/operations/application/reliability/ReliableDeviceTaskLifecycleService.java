package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskLifecyclePort;
import org.enveloping.ecobin.device.api.port.DeviceCommandCanonicalizationPort;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;
import java.util.Optional;
import java.util.Map;
import java.time.ZoneOffset;
import java.time.Instant;
import java.util.Locale;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

@Service
@Transactional(propagation = Propagation.MANDATORY)
public class ReliableDeviceTaskLifecycleService implements ReliableDeviceTaskLifecyclePort {
    private final ReliableOperationsJdbcRepository repository;
    private final ReliableWorkSignal workSignal;
    private final ObjectMapper objectMapper;
    private final DeviceCommandCanonicalizationPort canonicalizer;

    public ReliableDeviceTaskLifecycleService(ReliableOperationsJdbcRepository repository,
            ReliableWorkSignal workSignal, ObjectMapper objectMapper, DeviceCommandCanonicalizationPort canonicalizer) {
        this.repository = repository;
        this.workSignal = workSignal;
        this.objectMapper = objectMapper;
        this.canonicalizer = canonicalizer;
    }

    @Override
    public boolean externalCallMayHaveStarted(UUID taskUid) {
        return repository.externalCallMayHaveStarted(taskUid);
    }

    @Override
    public void cancelDeviceWork(String hardwareSn) {
        repository.cancelDeviceWork(repository.requireDeviceAssetId(hardwareSn), repository.databaseNow());
    }

    @Override
    public void pauseDeviceWork(String hardwareSn) {
        repository.pauseDeviceWork(repository.requireDeviceAssetId(hardwareSn), repository.databaseNow());
    }

    @Override
    public void resumeDeviceWork(String hardwareSn) {
        repository.resumeDeviceWork(repository.requireDeviceAssetId(hardwareSn), repository.databaseNow());
        workSignal.deviceCommand();
    }

    @Override
    public Optional<RenewedUpgradeCommand> renewExpiredUnsentUpgradeTask(String hardwareSn, UUID taskUid) {
        long assetId = repository.requireDeviceAssetId(hardwareSn);
        var stored = repository.upgradeTaskToResume(assetId, taskUid);
        if (stored.isEmpty()) return Optional.empty();
        var old = stored.get();
        if (!repository.lockDeviceWorkAllowed(assetId, old.taskType())) return Optional.empty();
        var now = repository.databaseNow();
        ObjectNode envelope = (ObjectNode) objectMapper.readTree(old.envelopeJson());
        if (Instant.parse(envelope.path("expiresAt").asText()).isAfter(now.toInstant(ZoneOffset.UTC))) {
            return Optional.empty();
        }
        // A new authorization is permitted only if no previous attempt could
        // have reached the device. Keep the old immutable envelope for audit.
        if (repository.externalCallMayHaveStarted(taskUid)) return Optional.empty();
        UUID commandUid = UUID.randomUUID();
        envelope.put("commandUid", commandUid.toString());
        envelope.put("issuedAt", now.toInstant(ZoneOffset.UTC).toString());
        envelope.put("expiresAt", now.plusMinutes("START_MCU_FIRMWARE_UPDATE".equals(old.taskType()) ? 15 : 5)
                .toInstant(ZoneOffset.UTC).toString());
        Map<?, ?> semantic = objectMapper.convertValue(envelope, Map.class);
        UUID next = repository.insertPlatformDeviceControlTask(assetId, old.taskType(),
                old.taskType() + ":RESUME:" + commandUid.toString().toUpperCase(Locale.ROOT),
                old.targetType(), old.targetStableKey(), old.payloadSchemaVersion(),
                objectMapper.writeValueAsString(envelope), canonicalizer.payloadSha256(semantic),
                old.correlationUid(), commandUid, old.maxAutoAttempts(),
                repository.lockInitialDeviceDispatchWaitReason(assetId), now);
        repository.cancelReplacedUpgradeTask(taskUid, now);
        workSignal.deviceCommand();
        return Optional.of(new RenewedUpgradeCommand(next, commandUid));
    }
}
