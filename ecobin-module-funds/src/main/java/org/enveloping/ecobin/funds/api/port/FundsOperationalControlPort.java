package org.enveloping.ecobin.funds.api.port;

import java.time.LocalDateTime;
import java.util.Objects;
import java.util.UUID;

/**
 * funds 提交领域事实后，同事务刷新 operations 告警与可靠任务的窄边界。
 */
public interface FundsOperationalControlPort {

    void observePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime observedAt);

    void resolvePayoutLiquidityPause(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime resolvedAt);

    void markPayoutTaskWaiting(
            UUID taskUid,
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime waitingAt);

    int wakePayoutTasks(
            long merchantProfileId,
            UUID pausedEventUid,
            LocalDateTime wakeAt);

    WithdrawalSubmitTaskWakeResult wakeWithdrawalSubmitTask(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            LocalDateTime wakeAt);

    WithdrawalSubmitTaskWakeResult scheduleWithdrawalChannelQuery(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            LocalDateTime wakeAt);

    WithdrawalSubmitTaskWakeResult
    recoverWithdrawalSubmitAfterConfirmedNotFound(
            long tenantId,
            long organizationId,
            String withdrawalNo,
            LocalDateTime wakeAt);

    void observeReconciliationIssue(ReconciliationIssue issue);

    enum WithdrawalSubmitTaskWakeResult {
        /** 空闲任务已立即排队，或租约中任务已推进唤醒版本。 */
        WOKEN,
        /** 任务仍在等待出款闸门等其他独立条件，本次不能立即派发。 */
        WAITING_ON_ANOTHER_CONDITION,
        /** 精确任务缺失，或其当前状态不满足本次受控恢复条件。 */
        NOT_WAKEABLE
    }

    record ReconciliationIssue(
            long tenantId,
            long organizationId,
            long sourceTaskAttemptId,
            String issueCode,
            String severity,
            String subjectType,
            String subjectStableKey,
            byte[] initialEvidenceSha256,
            String redactedEvidenceSummary,
            LocalDateTime observedAt) {

        public ReconciliationIssue {
            if (tenantId <= 0 || organizationId <= 0
                    || sourceTaskAttemptId <= 0) {
                throw new IllegalArgumentException(
                        "reconciliation scope and source must be positive");
            }
            requireText(issueCode, "issueCode");
            if (!java.util.List.of("INFO", "WARNING", "CRITICAL")
                    .contains(severity)) {
                throw new IllegalArgumentException("severity is invalid");
            }
            requireText(subjectType, "subjectType");
            requireText(subjectStableKey, "subjectStableKey");
            if (initialEvidenceSha256 == null
                    || initialEvidenceSha256.length != 32) {
                throw new IllegalArgumentException(
                        "initialEvidenceSha256 must contain 32 bytes");
            }
            initialEvidenceSha256 = initialEvidenceSha256.clone();
            requireText(redactedEvidenceSummary,
                    "redactedEvidenceSummary");
            Objects.requireNonNull(observedAt, "observedAt");
        }

        @Override
        public byte[] initialEvidenceSha256() {
            return initialEvidenceSha256.clone();
        }

        private static void requireText(String value, String field) {
            if (value == null || value.isBlank()) {
                throw new IllegalArgumentException(
                        field + " must not be blank");
            }
        }
    }
}
