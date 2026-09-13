package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.web.v1.DeviceModels.AcceptanceEvidenceSummary;
import org.enveloping.ecobin.device.web.v1.DeviceModels.FactoryProgressView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.ReliableTaskAttemptSummary;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class FactoryProgressQueryServiceTest {

    private static final Instant FETCHED_AT =
            Instant.parse("2026-08-31T00:00:00Z");
    private static final AcceptanceEvidenceSummary PASSED_A =
            evidence("11111111-1111-4111-8111-111111111111",
                    "PASSED", "aa", "2026-08-30T00:00:00Z");
    private static final AcceptanceEvidenceSummary FAILED_B =
            evidence("22222222-2222-4222-8222-222222222222",
                    "FAILED", "bb", "2026-08-30T01:00:00Z");

    @Test
    void retiredDevicesNeverRecommendRestorationOrNewFactoryWork() {
        for (String sealStatus : List.of("SEALED", "CANCELLED")) {
            var view = project(asset("PASSED", 8, List.of(), true, "RETIRED"),
                    2, PASSED_A, PASSED_A, task("CANCELLED", null),
                    seal(sealStatus, "DONE", null, null));
            assertThat(view.blockingCode()).isEqualTo("DEVICE_ASSET_RETIRED");
            assertThat(view.nextActionCodes()).isEmpty();
            assertThat(view.seal().status()).isEqualTo(sealStatus);
        }
    }

    @Test
    void noFactoryBagWaitsForOperatorScanning() {
        FactoryProgressView view = project(
                asset("PENDING", 0, List.of(), false),
                0,
                null,
                null,
                null,
                null);

        assertThat(view.factoryBags().expectedPortCount()).isEqualTo(2);
        assertThat(view.factoryBags().verifiedCount()).isZero();
        assertThat(view.factoryBags().complete()).isFalse();
        assertThat(view.currentStage()).isEqualTo("FACTORY_BAGS");
        assertThat(view.status()).isEqualTo("WAITING_OPERATOR");
        assertThat(view.blockingCode())
                .isEqualTo("FACTORY_BAGS_INCOMPLETE");
        assertThat(view.nextActionCodes())
                .containsExactly("SCAN_FACTORY_BAGS");
    }

    @Test
    void pendingAcceptanceExposesReliableTaskAndLatestAttempt() {
        UUID taskUid = UUID.fromString(
                "33333333-3333-4333-8333-333333333333");
        ReliableTaskAttemptSummary attempt = new ReliableTaskAttemptSummary(
                3L,
                "RETRYABLE_FAILURE",
                503,
                "10500",
                "request-42",
                "OneNet temporary failure",
                Instant.parse("2026-08-30T02:00:00Z"));
        FactoryProgressQueryService.TaskProgressRow task =
                new FactoryProgressQueryService.TaskProgressRow(
                        taskUid, "PENDING", null, null, attempt);

        FactoryProgressView view = project(
                asset("PENDING", 0, List.of(), false),
                2,
                null,
                null,
                task,
                null);

        assertThat(view.currentStage()).isEqualTo("MACHINE_ACCEPTANCE");
        assertThat(view.status()).isEqualTo("IN_PROGRESS");
        assertThat(view.acceptanceRequest().taskUid()).isEqualTo(taskUid);
        assertThat(view.acceptanceRequest().latestAttempt())
                .isEqualTo(attempt);
        assertThat(view.nextActionCodes())
                .containsExactly("WAIT_FOR_ACCEPTANCE_EVIDENCE");
    }

    @Test
    void failedAcceptanceUsesAuthoritativeAssetReasonsWithoutEvidence() {
        FactoryProgressView view = project(
                asset(
                        "FAILED",
                        2,
                        List.of("ACCEPTANCE_EVIDENCE_MISSING"),
                        false),
                2,
                null,
                null,
                task("DONE", null),
                null);

        assertThat(view.acceptance().authoritativeEvidence()).isNull();
        assertThat(view.acceptance().currentFailureReasons())
                .containsExactly("ACCEPTANCE_EVIDENCE_MISSING");
        assertThat(view.status()).isEqualTo("BLOCKED");
        assertThat(view.blockingCode())
                .isEqualTo("ACCEPTANCE_EVIDENCE_MISSING");
    }

    @Test
    void passedAcceptanceKeepsBoundEvidenceSeparateFromNewerFailure() {
        FactoryProgressView view = project(
                asset("PASSED", 4, List.of(), true),
                2,
                PASSED_A,
                FAILED_B,
                task("DONE", null),
                null);

        assertThat(view.acceptance().authoritativeEvidence())
                .isEqualTo(PASSED_A);
        assertThat(view.acceptance().latestEvidence())
                .isEqualTo(FAILED_B);
        assertThat(view.acceptance().status()).isEqualTo("PASSED");
        assertThat(view.currentStage())
                .isEqualTo("FACTORY_SEAL_AUTHORIZATION");
        assertThat(view.seal().status()).isEqualTo("NOT_ISSUED");
    }

    @Test
    void sealTaskDoneNeverMeansSealed() {
        FactoryProgressView view = passedWithSeal(seal(
                "PENDING", "DONE", null, null));

        assertThat(view.seal().taskState()).isEqualTo("DONE");
        assertThat(view.seal().status()).isEqualTo("PENDING");
        assertThat(view.currentStage())
                .isEqualTo("FACTORY_SEAL_AUTHORIZATION");
        assertThat(view.status()).isEqualTo("IN_PROGRESS");
    }

    @Test
    void pendingSealTaskRemainsInProgress() {
        FactoryProgressView view = passedWithSeal(seal(
                "PENDING", "PENDING", null, null));

        assertThat(view.seal().status()).isEqualTo("PENDING");
        assertThat(view.seal().taskState()).isEqualTo("PENDING");
        assertThat(view.status()).isEqualTo("IN_PROGRESS");
        assertThat(view.nextActionCodes()).containsExactly(
                "WAIT_FOR_FACTORY_SEAL_ACKNOWLEDGEMENT");
    }

    @Test
    void blockedSealExposesReasonAndSafeReliableTaskAction() {
        FactoryProgressView view = passedWithSeal(seal(
                "PENDING",
                "BLOCKED",
                "DEVICE_IDENTITY_UNRESOLVED",
                null));

        assertThat(view.status()).isEqualTo("BLOCKED");
        assertThat(view.blockingCode())
                .isEqualTo("DEVICE_IDENTITY_UNRESOLVED");
        assertThat(view.nextActionCodes()).containsExactly(
                "OPEN_RELIABLE_TASK",
                "RESOLVE_FACTORY_SEAL_TASK_BLOCKER");
    }

    @Test
    void acknowledgedSealWaitsForLocalOperatorConfirmation() {
        FactoryProgressView view = passedWithSeal(seal(
                "ACKNOWLEDGED", "DONE", null, null));

        assertThat(view.currentStage()).isEqualTo("END_FACTORY_MODE");
        assertThat(view.status()).isEqualTo("WAITING_OPERATOR");
        assertThat(view.nextActionCodes())
                .containsExactly("CONFIRM_END_FACTORY_MODE");
    }

    @Test
    void cancelledSealRemainsBlockedWithCancellationReason() {
        FactoryProgressView view = passedWithSeal(seal(
                "CANCELLED",
                "CANCELLED",
                null,
                "ACCEPTANCE_EVIDENCE_NOT_LATEST"));

        assertThat(view.status()).isEqualTo("BLOCKED");
        assertThat(view.blockingCode())
                .isEqualTo("ACCEPTANCE_EVIDENCE_NOT_LATEST");
        assertThat(view.seal().cancellationReason())
                .isEqualTo("ACCEPTANCE_EVIDENCE_NOT_LATEST");
    }

    @Test
    void sealedRequiresAuthoritativeSealStatusNotTaskState() {
        FactoryProgressView view = passedWithSeal(seal(
                "SEALED", "DONE", null, null));

        assertThat(view.currentStage()).isEqualTo("FACTORY_SEALED");
        assertThat(view.status()).isEqualTo("COMPLETED");
        assertThat(view.blockingCode()).isNull();
        assertThat(view.nextActionCodes()).isEmpty();
        assertThat(view.seal().completionReceivedAt()).isNotNull();
    }

    @Test
    void unknownAcceptanceFailureCodeIsPreserved() {
        FactoryProgressView view = project(
                asset("FAILED", 7, List.of("NEW_UNKNOWN_CODE"), false),
                2,
                null,
                FAILED_B,
                task("DONE", null),
                null);

        assertThat(view.acceptance().currentFailureReasons())
                .containsExactly("NEW_UNKNOWN_CODE");
        assertThat(view.blockingCode()).isEqualTo("NEW_UNKNOWN_CODE");
    }

    @Test
    void factorySealAuthorizationRejectionRemainsAnAssetFailureReason() {
        FactoryProgressView view = project(
                asset(
                        "FAILED",
                        9,
                        List.of("FACTORY_SEAL_AUTHORIZATION_REJECTED"),
                        false),
                2,
                null,
                PASSED_A,
                task("DONE", null),
                null);

        assertThat(view.acceptance().currentFailureReasons())
                .containsExactly("FACTORY_SEAL_AUTHORIZATION_REJECTED");
        assertThat(view.blockingCode())
                .isEqualTo("FACTORY_SEAL_AUTHORIZATION_REJECTED");
    }

    @Test
    void verifiedBagCountUsesTheAuthoritativeFactoryVerificationPredicate() {
        String sql = FactoryProgressQueryService
                .VERIFIED_FACTORY_BAG_COUNT_SQL;

        assertThat(sql)
                .contains("installation_source = 'LEGACY_GRANDFATHERED'")
                .contains("installation_source = 'FACTORY_MINIAPP'")
                .contains("installed_by_factory_operator_id IS NOT NULL")
                .contains("label_item_id IS NOT NULL")
                .contains("FROM rec_bag_label_claim claim")
                .contains("claim.asset_id = bag.asset_id")
                .contains("claim.port_no = bag.port_no")
                .contains("claim.released_at IS NULL")
                .doesNotContain("installation_source = 'PLATFORM_CREATE'");
    }

    @Test
    void acceptanceTaskQueryBindsTheCurrentBagRevisionAndDigest() {
        String sql = FactoryProgressQueryService.CURRENT_ACCEPTANCE_TASK_SQL;

        assertThat(sql)
                .contains("task.scope_kind = 'PLATFORM'")
                .contains("task.tenant_id IS NULL")
                .contains("task.organization_id IS NULL")
                .contains("$.payload.factoryBagRevision")
                .contains("$.payload.factoryBagSetSha256")
                .contains("ORDER BY task.id DESC")
                .contains("LIMIT 1");
    }

    private static FactoryProgressView passedWithSeal(
            FactoryProgressQueryService.SealProgressRow seal) {
        return project(
                asset("PASSED", 8, List.of(), true),
                2,
                PASSED_A,
                PASSED_A,
                task("DONE", null),
                seal);
    }

    private static FactoryProgressView project(
            FactoryProgressQueryService.AssetProgressRow asset,
            int bagCount,
            AcceptanceEvidenceSummary authoritative,
            AcceptanceEvidenceSummary latest,
            FactoryProgressQueryService.TaskProgressRow task,
            FactoryProgressQueryService.SealProgressRow seal) {
        return FactoryProgressQueryService.project(
                new FactoryProgressQueryService.ProgressSource(
                        asset,
                        bagCount,
                        authoritative,
                        latest,
                        task,
                        seal),
                FETCHED_AT);
    }

    private static FactoryProgressQueryService.AssetProgressRow asset(
            String status,
            long generation,
            List<String> failures,
            boolean hasEvidenceSha) {
        return asset(status, generation, failures, hasEvidenceSha, "NORMAL");
    }

    private static FactoryProgressQueryService.AssetProgressRow asset(
            String status, long generation, List<String> failures,
            boolean hasEvidenceSha, String lifecycle) {
        return new FactoryProgressQueryService.AssetProgressRow(
                41L,
                2,
                5L,
                new byte[32],
                status,
                generation,
                failures,
                hasEvidenceSha ? new byte[32] : null,
                LocalDateTime.parse("2026-08-30T00:00:00"),
                "PASSED".equals(status)
                        ? LocalDateTime.parse("2026-08-30T00:00:01")
                        : null,
                lifecycle);
    }

    private static FactoryProgressQueryService.TaskProgressRow task(
            String state,
            String reason) {
        return new FactoryProgressQueryService.TaskProgressRow(
                UUID.fromString(
                        "44444444-4444-4444-8444-444444444444"),
                state,
                reason,
                null,
                null);
    }

    private static FactoryProgressQueryService.SealProgressRow seal(
            String status,
            String taskState,
            String blockedReason,
            String cancellationReason) {
        boolean acknowledged = "ACKNOWLEDGED".equals(status)
                || "SEALED".equals(status);
        boolean sealed = "SEALED".equals(status);
        return new FactoryProgressQueryService.SealProgressRow(
                status,
                8L,
                cancellationReason,
                UUID.fromString(
                        "55555555-5555-4555-8555-555555555555"),
                taskState,
                blockedReason,
                blockedReason == null ? null : "redacted blocker",
                null,
                acknowledged
                        ? LocalDateTime.parse("2026-08-30T03:00:00")
                        : null,
                sealed
                        ? LocalDateTime.parse("2026-08-30T04:00:00")
                        : null,
                sealed
                        ? LocalDateTime.parse("2026-08-30T04:00:01")
                        : null,
                sealed
                        ? LocalDateTime.parse("2026-08-30T04:00:02")
                        : null);
    }

    private static AcceptanceEvidenceSummary evidence(
            String uid,
            String status,
            String sha,
            String receivedAt) {
        return new AcceptanceEvidenceSummary(
                UUID.fromString(uid),
                status,
                sha,
                Instant.parse(receivedAt));
    }
}
