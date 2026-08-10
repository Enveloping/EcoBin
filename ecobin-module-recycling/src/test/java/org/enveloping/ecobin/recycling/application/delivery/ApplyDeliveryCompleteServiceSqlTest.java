package org.enveloping.ecobin.recycling.application.delivery;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class ApplyDeliveryCompleteServiceSqlTest {

    @Test
    void locksBagOccupancyBeforePlainImmutableBagRead() {
        assertThat(upper(
                ApplyDeliveryCompleteService
                        .LOCK_CURRENT_BAG_OCCUPANCY_SQL))
                .contains(
                        "FROM REC_BAG_CURRENT_OCCUPANCY",
                        "FOR UPDATE");
        assertThat(upper(
                ApplyDeliveryCompleteService.LOAD_FROZEN_BAG_SQL))
                .contains("FROM REC_BAG")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    void capacityProjectionReadsTheCurrentBagAndBaselineBag() {
        String sql = Arrays.stream(
                        ApplyDeliveryCompleteService.class
                                .getDeclaredFields())
                .filter(field -> Modifier.isStatic(
                        field.getModifiers()))
                .filter(field -> field.getType() == String.class)
                .map(ApplyDeliveryCompleteServiceSqlTest::readString)
                .map(ApplyDeliveryCompleteServiceSqlTest::upper)
                .reduce("", (left, right) -> left + "\n" + right);

        assertThat(sql)
                .contains(
                        "FROM REC_PORT_CAPACITY_STATE",
                        "CURRENT_BAG_ID",
                        "CURRENT_BASELINE_ID",
                        "FROM REC_PORT_WEIGHT_BASELINE",
                        "BAG_ID",
                        "BASELINE_WEIGHT_G");
    }

    @Test
    void staleCapacityGenerationSkipsOnlyTheCapacityProjection() {
        var exact = capacity(41L, "VALID", 51L, 41L);
        var oldCapacityBag = capacity(40L, "VALID", 51L, 40L);
        var oldBaselineBag = capacity(41L, "VALID", 51L, 40L);
        var invalidCurrentBaseline = capacity(
                41L,
                "INVALID",
                null,
                null);

        assertThat(ApplyDeliveryCompleteService
                .capacityProjectionBlockReason(41L, exact)).isNull();
        assertThat(ApplyDeliveryCompleteService
                .capacityProjectionBlockReason(41L, oldCapacityBag))
                .isEqualTo("CAPACITY_CURRENT_BAG_MISMATCH");
        assertThat(ApplyDeliveryCompleteService
                .capacityProjectionBlockReason(41L, oldBaselineBag))
                .isEqualTo("WEIGHT_BASELINE_GENERATION_MISMATCH");
        assertThat(ApplyDeliveryCompleteService
                .capacityProjectionBlockReason(
                        41L,
                        invalidCurrentBaseline)).isNull();
        assertThat(ApplyDeliveryCompleteService
                .capacityProjectionBlockReason(41L, null))
                .isEqualTo("CAPACITY_STATE_MISSING");
    }

    private static ApplyDeliveryCompleteService.CapacityState capacity(
            Long capacityBagId,
            String baselineState,
            Long baselineId,
            Long baselineBagId) {
        Long weight = baselineId == null ? null : 100L;
        return new ApplyDeliveryCompleteService.CapacityState(
                capacityBagId,
                baselineState,
                baselineId,
                weight,
                baselineId,
                baselineBagId,
                weight,
                "READY",
                null,
                null,
                "NOT_FULL");
    }

    private static String readString(Field field) {
        try {
            field.setAccessible(true);
            return (String) field.get(null);
        } catch (IllegalAccessException exception) {
            throw new AssertionError(exception);
        }
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
