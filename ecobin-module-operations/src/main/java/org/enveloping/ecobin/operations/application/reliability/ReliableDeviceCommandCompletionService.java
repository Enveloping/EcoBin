package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.ExpiredUnstartedDeviceWorkPort;
import org.enveloping.ecobin.device.api.port.BlockedDeviceCommandBusinessPort;
import org.enveloping.ecobin.device.api.result.DeviceCommandSubmissionResult;
import org.enveloping.ecobin.device.api.result.BlockedDeviceCommand;
import org.enveloping.ecobin.operations.infrastructure.config.ReliableTaskProperties;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.DeviceTaskExecution;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

@Service
public class ReliableDeviceCommandCompletionService {

    private final ReliableOperationsJdbcRepository repository;
    private final ReliableTaskProperties properties;
    private final ObjectMapper objectMapper;
    private final List<ExpiredUnstartedDeviceWorkPort>
            expiredUnstartedWorkPorts;
    private final List<BlockedDeviceCommandBusinessPort>
            blockedCommandBusinessPorts;

    private static final Set<String> SAFE_CONTROL_COMMANDS = Set.of(
            "CONFIRM_EDGE_EVENT",
            "PROVIDE_PHOTO_UPLOAD_GRANT",
            "AUTHORIZE_FACTORY_SEAL");
    private static final Set<String> TRANSPORT_COMPLETES_COMMANDS = Set.of(
            "SYNC_DEVICE_ENTRY_URL");
    private static final Duration CONFIGURATION_EVIDENCE_WINDOW =
            Duration.ofMinutes(2);
    private static final Duration BASELINE_EVIDENCE_GRACE =
            Duration.ofSeconds(30);
    private static final Duration PHYSICAL_EVIDENCE_GRACE =
            Duration.ofSeconds(30);
    private static final Duration BUSINESS_UPDATE_EVIDENCE_GRACE =
            Duration.ofMinutes(5);
    private static final Duration BUSINESS_UPDATE_CANCEL_EVIDENCE_WINDOW =
            Duration.ofMinutes(35);
    private static final long MIN_BASELINE_MEASUREMENT_TIMEOUT_MS = 1_000;
    private static final long MAX_BASELINE_MEASUREMENT_TIMEOUT_MS = 6_000;
    private static final long MIN_BUSINESS_UPDATE_STAGE_SECONDS = 60;
    private static final long MAX_BUSINESS_UPDATE_STAGE_SECONDS = 86_400;
    private static final int MAX_BUSINESS_UPDATE_ATTEMPTS = 10;

    public ReliableDeviceCommandCompletionService(
            ReliableOperationsJdbcRepository repository,
            ReliableTaskProperties properties,
            ObjectMapper objectMapper,
            List<ExpiredUnstartedDeviceWorkPort>
                    expiredUnstartedWorkPorts,
            List<BlockedDeviceCommandBusinessPort>
                    blockedCommandBusinessPorts) {
        this.repository = repository;
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.expiredUnstartedWorkPorts = List.copyOf(
                expiredUnstartedWorkPorts);
        this.blockedCommandBusinessPorts = List.copyOf(
                blockedCommandBusinessPorts);
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public void complete(
            ClaimedDeviceCommandTask claim,
            DeviceCommandSubmissionResult result,
            long durationMillis) {
        DeviceTaskExecution execution =
                repository.lockDeviceTaskExecution(
                        claim.taskUid(),
                        claim.commandUid(),
                        claim.attemptUid());
        if (!execution.attemptLeaseToken().equals(claim.leaseToken())
                || execution.claimedWakeVersion()
                        != claim.claimedWakeVersion()) {
            throw new ReliableTaskInvariantException(
                    "device completion does not match the claimed attempt");
        }
        String technicalResult = technicalResult(result.outcome());
        if (execution.technicalResult() == null) {
            repository.recordDeviceAttemptResult(
                    execution.attemptId(),
                    technicalResult,
                    durationMillis,
                    result.requestSha256(),
                    result.responseSha256(),
                    result.httpStatus(),
                    result.externalErrorCode(),
                    result.externalRequestId(),
                    result.redactedDiagnostic(),
                    repository.databaseNow());
        } else if (!technicalResult.equals(execution.technicalResult())) {
            throw new ReliableTaskInvariantException(
                    "device attempt already carries a different result");
        }

        if (!"PENDING".equals(execution.state())) {
            return;
        }
        boolean ownsLease = claim.leaseToken()
                .equals(execution.currentLeaseToken());
        if (!ownsLease) {
            return;
        }
        var now = repository.databaseNow();
        if (execution.wakeVersion() != claim.claimedWakeVersion()) {
            repository.releaseForImmediateRecheck(execution.taskId(), now);
            return;
        }

        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.TARGET_OFFLINE) {
            repository.releaseForDispatchWait(
                    execution.taskId(), "DEVICE_OFFLINE", now);
            return;
        }

        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.TARGET_NOT_FOUND) {
            projectBlockedCommand(
                    claim.commandUid(),
                    claim.commandType(),
                    "DEVICE_IDENTITY_UNRESOLVED",
                    now);
            repository.blockDeviceTask(
                    execution.taskId(),
                    execution.consecutiveFailureCount() + 1,
                    execution.wakeVersion(),
                    "DEVICE_IDENTITY_UNRESOLVED",
                    "OneNet cannot resolve product_id and device_name",
                    now);
            return;
        }

        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PERMANENT_FAILURE) {
            projectBlockedCommand(
                    claim.commandUid(),
                    claim.commandType(),
                    "PERMANENT_TECHNICAL_FAILURE",
                    now);
            repository.blockDeviceTask(
                    execution.taskId(),
                    execution.consecutiveFailureCount() + 1,
                    execution.wakeVersion(),
                    "PERMANENT_TECHNICAL_FAILURE",
                    "frozen device command was permanently rejected by transport",
                    now);
            return;
        }
        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                && TRANSPORT_COMPLETES_COMMANDS.contains(
                claim.commandType())) {
            // This command changes no platform business state and starts no
            // physical work. OneNet acceptance completes the cloud delivery
            // intent; the edge inbox remains the durable local hand-off.
            repository.markTaskDone(
                    execution.taskId(), execution.wakeVersion(), now);
            return;
        }
        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                && !SAFE_CONTROL_COMMANDS.contains(claim.commandType())) {
            repository.scheduleAwaitingDeviceEvidence(
                    execution.taskId(),
                    evidenceDeadline(claim, now),
                    now);
            return;
        }
        if (execution.attemptsForCurrentWake()
                >= execution.maxAutoAttempts()) {
            String reason = result.outcome()
                    == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                    ? "DEVICE_CONFIRMATION_TIMEOUT"
                    : "AUTO_RETRY_EXHAUSTED";
            String diagnostic = result.outcome()
                    == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED
                    ? "platform accepted submissions but no trusted device proof arrived"
                    : "automatic device submission retry limit reached";
            projectBlockedCommand(
                    claim.commandUid(),
                    claim.commandType(),
                    reason,
                    now);
            repository.blockDeviceTask(
                    execution.taskId(),
                    execution.consecutiveFailureCount()
                            + (result.outcome()
                            == DeviceCommandSubmissionResult.Outcome
                                    .RETRYABLE_FAILURE ? 1 : 0),
                    execution.wakeVersion(),
                    reason,
                    diagnostic,
                    now);
            return;
        }

        Duration backoff = exponentialBackoff(
                properties.getIotDevice().getInitialBackoff(),
                properties.getIotDevice().getMaximumBackoff(),
                execution.attemptsForCurrentWake());
        if (result.outcome()
                == DeviceCommandSubmissionResult.Outcome.PLATFORM_ACCEPTED) {
            repository.scheduleRetry(
                    execution.taskId(),
                    execution.consecutiveFailureCount(),
                    now.plus(backoff),
                    now);
        } else {
            repository.scheduleRetry(
                    execution.taskId(),
                    execution.consecutiveFailureCount() + 1,
                    now.plus(backoff),
                    now);
        }
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public int expireEvidenceWaits() {
        LocalDateTime now = repository.databaseNow();
        int closed = 0;
        for (ExpiredUnstartedDeviceWorkPort port
                : expiredUnstartedWorkPorts) {
            List<Long> commandIds =
                    port.closeExpiredUnstartedWork(now);
            for (long commandId : commandIds) {
                repository.cancelExpiredUnstartedDeviceTask(
                        commandId, now);
            }
            closed += commandIds.size();
        }
        int legacyDeadlines = 0;
        for (var task : repository.lockLegacyBaselineEvidenceWaits(now)) {
            LocalDateTime deadline = evidenceDeadline(
                    task.commandType(),
                    task.semanticEnvelopeJson(),
                    task.acceptedAt());
            if (deadline.isAfter(now)) {
                repository.rescheduleLegacyBaselineEvidenceWait(
                        task.taskId(),
                        task.wakeVersion(),
                        deadline,
                        now);
            } else {
                projectBlockedCommand(
                        task.commandUid(),
                        task.commandType(),
                        "DEVICE_EVIDENCE_TIMEOUT",
                        now);
                repository.blockExpiredDeviceEvidenceWait(
                        task.taskId(), task.wakeVersion(), now);
            }
            legacyDeadlines++;
        }
        var expiredEvidence =
                repository.lockExpiredDeviceEvidenceWaits(now);
        for (var task : expiredEvidence) {
            projectBlockedCommand(
                    task.commandUid(),
                    task.commandType(),
                    task.reasonCode(),
                    now);
            repository.blockExpiredDeviceEvidenceWait(
                    task.taskId(), task.wakeVersion(), now);
        }
        int reconciled = 0;
        for (var task : repository.lockUnalignedBlockedPhysicalCommands()) {
            projectBlockedCommand(
                    task.commandUid(),
                    task.commandType(),
                    task.reasonCode(),
                    task.blockedAt() == null ? now : task.blockedAt());
            reconciled++;
        }
        return closed + legacyDeadlines + expiredEvidence.size()
                + reconciled;
    }

    private void projectBlockedCommand(
            java.util.UUID commandUid,
            String commandType,
            String reasonCode,
            LocalDateTime blockedAt) {
        BlockedDeviceCommand.Certainty certainty = Set.of(
                "DEVICE_IDENTITY_UNRESOLVED",
                "PERMANENT_TECHNICAL_FAILURE")
                .contains(reasonCode)
                ? BlockedDeviceCommand.Certainty.DEFINITELY_NOT_ACCEPTED
                : BlockedDeviceCommand.Certainty.OUTCOME_UNKNOWN;
        var blocked = new BlockedDeviceCommand(
                commandUid,
                commandType,
                certainty,
                reasonCode,
                blockedAt);
        blockedCommandBusinessPorts.forEach(
                port -> port.applyBlockedDeviceCommand(blocked));
    }

    private LocalDateTime evidenceDeadline(
            ClaimedDeviceCommandTask claim,
            LocalDateTime now) {
        return evidenceDeadline(
                claim.commandType(), claim.semanticEnvelopeJson(), now);
    }

    private LocalDateTime evidenceDeadline(
            String commandType,
            String semanticEnvelopeJson,
            LocalDateTime now) {
        if ("APPLY_CONFIGURATION".equals(commandType)) {
            return now.plus(CONFIGURATION_EVIDENCE_WINDOW);
        }
        if ("CANCEL_BUSINESS_RUNTIME_UPDATE".equals(commandType)) {
            return now.plus(BUSINESS_UPDATE_CANCEL_EVIDENCE_WINDOW);
        }
        try {
            JsonNode root = objectMapper.readTree(
                    semanticEnvelopeJson);
            if ("MEASURE_EMPTY_BAG_BASELINE".equals(
                    commandType)) {
                JsonNode timeout = root.path("payload")
                        .get("measurementTimeoutMs");
                if (timeout == null
                        || !timeout.isIntegralNumber()
                        || !timeout.canConvertToLong()) {
                    throw new ReliableTaskInvariantException(
                            "baseline evidence timeout is missing or invalid");
                }
                long timeoutMs = timeout.longValue();
                if (timeoutMs < MIN_BASELINE_MEASUREMENT_TIMEOUT_MS
                        || timeoutMs
                        > MAX_BASELINE_MEASUREMENT_TIMEOUT_MS) {
                    throw new ReliableTaskInvariantException(
                            "baseline evidence timeout is outside the frozen contract");
                }
                return now.plus(BASELINE_EVIDENCE_GRACE)
                        .plus(Duration.ofMillis(timeoutMs));
            }
            if ("START_BUSINESS_RUNTIME_UPDATE".equals(commandType)) {
                JsonNode payload = root.path("payload");
                long downloadSeconds = requiredBoundedLong(
                        payload,
                        "downloadTimeoutSeconds",
                        MIN_BUSINESS_UPDATE_STAGE_SECONDS,
                        MAX_BUSINESS_UPDATE_STAGE_SECONDS);
                long drainSeconds = requiredBoundedLong(
                        payload,
                        "drainTimeoutSeconds",
                        MIN_BUSINESS_UPDATE_STAGE_SECONDS,
                        MAX_BUSINESS_UPDATE_STAGE_SECONDS);
                long observationSeconds = requiredBoundedLong(
                        payload,
                        "observationWindowSeconds",
                        MIN_BUSINESS_UPDATE_STAGE_SECONDS,
                        MAX_BUSINESS_UPDATE_STAGE_SECONDS);
                long maximumAttempts = requiredBoundedLong(
                        payload,
                        "maximumRetryCount",
                        0,
                        MAX_BUSINESS_UPDATE_ATTEMPTS);
                maximumAttempts = Math.max(1, maximumAttempts);
                Duration window = Duration.ofSeconds(downloadSeconds)
                        .multipliedBy(maximumAttempts)
                        .plusSeconds(drainSeconds)
                        .plusSeconds(observationSeconds)
                        .plus(BUSINESS_UPDATE_EVIDENCE_GRACE);
                return now.plus(window);
            }
            JsonNode value = root.get("expiresAt");
            if (value == null || !value.isTextual()) {
                return now.plus(PHYSICAL_EVIDENCE_GRACE);
            }
            LocalDateTime deadline = LocalDateTime.ofInstant(
                    Instant.parse(value.asText()),
                    ZoneOffset.UTC).plus(PHYSICAL_EVIDENCE_GRACE);
            return deadline.isAfter(now) ? deadline : now;
        } catch (RuntimeException invalidFrozenEnvelope) {
            throw new ReliableTaskInvariantException(
                    "device command evidence deadline is invalid");
        }
    }

    private static long requiredBoundedLong(
            JsonNode object,
            String field,
            long minimum,
            long maximum) {
        JsonNode value = object.get(field);
        if (value == null
                || !value.isIntegralNumber()
                || !value.canConvertToLong()) {
            throw new ReliableTaskInvariantException(
                    "business update evidence field is missing or invalid");
        }
        long result = value.longValue();
        if (result < minimum || result > maximum) {
            throw new ReliableTaskInvariantException(
                    "business update evidence field is outside the frozen contract");
        }
        return result;
    }

    private static String technicalResult(
            DeviceCommandSubmissionResult.Outcome outcome) {
        return switch (outcome) {
            case PLATFORM_ACCEPTED -> "TECHNICAL_SUCCESS";
            case TARGET_OFFLINE -> "TARGET_OFFLINE";
            case TARGET_NOT_FOUND -> "TARGET_NOT_FOUND";
            case RETRYABLE_FAILURE -> "RETRYABLE_FAILURE";
            case PERMANENT_FAILURE -> "PERMANENT_TECHNICAL_FAILURE";
        };
    }

    private static Duration exponentialBackoff(
            Duration initial, Duration maximum, int attemptCount) {
        long multiplier =
                1L << Math.min(30, Math.max(0, attemptCount - 1));
        try {
            Duration candidate = initial.multipliedBy(multiplier);
            return candidate.compareTo(maximum) > 0
                    ? maximum
                    : candidate;
        } catch (ArithmeticException overflow) {
            return maximum;
        }
    }
}
