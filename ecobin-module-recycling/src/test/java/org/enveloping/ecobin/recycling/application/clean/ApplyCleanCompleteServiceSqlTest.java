package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class ApplyCleanCompleteServiceSqlTest {

    @Test
    void removalUsesBothCurrentMeasurementsNotThePreviousBagBaseline() {
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(20_000L, 1_200L)).isEqualTo(18_800L);
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(500L, 100L)).isEqualTo(400L);
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(0L, 0L)).isZero();
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(100L, 200L)).isEqualTo(-100L);
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(null, 100L)).isNull();
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(500L, null)).isNull();
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(null, null)).isNull();
        assertThat(ApplyCleanCompleteService.calculateRemovedWeight(2_147_483_647L, -2_147_483_648L))
                .isEqualTo(4_294_967_295L);
    }

    @Test
    void completionJoinLocksOnlyMutableOperationRoot() {
        assertThat(ApplyCleanCompleteService.OPERATION_LOCK_CLAUSE)
                .isEqualTo("FOR UPDATE OF operation");
    }

    @Test
    void fixedFrameCompletionAcceptsItsSingleReportedWeightSample() {
        assertThat(ApplyCleanCompleteService
                .hasAtLeastOneReportedSample(1)).isTrue();
        assertThat(ApplyCleanCompleteService
                .hasAtLeastOneReportedSample(0)).isFalse();
    }

    @Test
    void capacityDuplicateBranchDoesNotRewriteImmutableIdentity() {
        String sql = ApplyCleanCompleteService
                .CAPACITY_NO_OP_DUPLICATE_CLAUSE
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("updated_at = rec_port_capacity_state.updated_at")
                .doesNotContain("port_id =");
    }

    @Test
    void terminalPhotoStatesAreRecognizedAsLaterLifecycleFacts() {
        assertThat(ApplyCleanCompleteService
                .isTerminalPhotoState("AVAILABLE")).isTrue();
        assertThat(ApplyCleanCompleteService
                .isTerminalPhotoState("PERMANENTLY_MISSING")).isTrue();
        assertThat(ApplyCleanCompleteService
                .isTerminalPhotoState("UPLOAD_PENDING")).isFalse();
    }

    @Test
    void bagOwnershipMovesByDeleteAndInsertInsteadOfUpdate() {
        String deleteSql = ApplyCleanCompleteService
                .DELETE_RESERVED_BAG_SLOT_SQL
                .toLowerCase(Locale.ROOT);
        String insertSql = ApplyCleanCompleteService
                .INSERT_PORT_BOUND_BAG_SLOT_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(deleteSql)
                .contains("delete from rec_bag_current_occupancy")
                .contains("occupancy_type = 'clean_reserved'");
        assertThat(insertSql)
                .contains("insert into rec_bag_current_occupancy")
                .contains("'port_bound'")
                .doesNotContain("update rec_bag_current_occupancy");
    }

    @Test
    void cleanConfirmationUsesOnlyWireContractReferenceTypes() {
        var references = ApplyCleanCompleteService
                .cleanCompletionResultReferences("CR-test");

        assertThat(references)
                .extracting(reference -> reference.type())
                .containsExactly("CLEAN_RECORD");
    }
}
