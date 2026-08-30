package org.enveloping.ecobin.operations.application.reliability;

import org.enveloping.ecobin.device.api.port.FactorySealDispatchAuthorizationPort;
import org.enveloping.ecobin.device.api.result.FactorySealDispatchDecision;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository;
import org.enveloping.ecobin.operations.infrastructure.persistence.reliability.ReliableOperationsJdbcRepository.DeviceTaskExecution;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class ReliableDeviceCommandAttemptService {

    private final ReliableOperationsJdbcRepository repository;
    private final FactorySealDispatchAuthorizationPort sealAuthorization;

    public ReliableDeviceCommandAttemptService(
            ReliableOperationsJdbcRepository repository,
            FactorySealDispatchAuthorizationPort sealAuthorization) {
        this.repository = repository;
        this.sealAuthorization = sealAuthorization;
    }

    @Transactional(
            propagation = Propagation.REQUIRES_NEW,
            isolation = Isolation.READ_COMMITTED)
    public DispatchPreparation prepareExternalCall(
            ClaimedDeviceCommandTask claim) {
        if ("AUTHORIZE_FACTORY_SEAL".equals(claim.commandType())) {
            var now = repository.databaseNow();
            FactorySealDispatchDecision decision =
                    sealAuthorization.authorizeDispatch(
                            claim.taskUid(),
                            claim.commandUid(),
                            claim.hardwareSn(),
                            now);
            if (decision.outcome()
                    != FactorySealDispatchDecision.Outcome.ALLOW) {
                finishWithoutExternalCall(claim, decision, now);
                return DispatchPreparation.NO_SUBMISSION;
            }
        }
        repository.markExternalCallMayHaveStarted(
                claim.attemptUid(),
                claim.leaseToken(),
                repository.databaseNow());
        return DispatchPreparation.SUBMIT;
    }

    private void finishWithoutExternalCall(
            ClaimedDeviceCommandTask claim,
            FactorySealDispatchDecision decision,
            java.time.LocalDateTime now) {
        DeviceTaskExecution execution = repository.lockDeviceTaskExecution(
                claim.taskUid(), claim.commandUid(), claim.attemptUid());
        if (!execution.attemptLeaseToken().equals(claim.leaseToken())
                || execution.claimedWakeVersion()
                        != claim.claimedWakeVersion()) {
            throw new ReliableTaskInvariantException(
                    "factory seal pre-dispatch check does not match claim");
        }
        if (execution.technicalResult() == null) {
            repository.recordDeviceAttemptResult(
                    execution.attemptId(),
                    "NO_ACTION_REQUIRED",
                    0,
                    null,
                    null,
                    null,
                    null,
                    null,
                    decision.reasonCode(),
                    now);
        }
        if ("DONE".equals(execution.state())
                || "CANCELLED".equals(execution.state())) {
            return;
        }
        if (!claim.leaseToken().equals(
                execution.currentLeaseToken())) {
            throw new ReliableTaskInvariantException(
                    "factory seal task lease changed during pre-dispatch check");
        }
        if (decision.outcome()
                == FactorySealDispatchDecision.Outcome
                        .ALREADY_ACKNOWLEDGED) {
            repository.markTaskDone(
                    execution.taskId(), execution.wakeVersion(), now);
        } else {
            repository.markTaskCancelled(
                    execution.taskId(), execution.wakeVersion(), now);
        }
    }

    public enum DispatchPreparation {
        SUBMIT,
        NO_SUBMISSION
    }
}
