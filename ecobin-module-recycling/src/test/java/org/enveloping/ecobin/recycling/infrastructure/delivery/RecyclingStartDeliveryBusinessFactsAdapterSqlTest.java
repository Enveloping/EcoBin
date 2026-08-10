package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.junit.jupiter.api.Test;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Arrays;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class RecyclingStartDeliveryBusinessFactsAdapterSqlTest {

    @Test
    void locksMutableHeadsAndSlotsButNotImmutableFacts() {
        assertThat(upper(
                RecyclingStartDeliveryBusinessFactsAdapter
                        .LOCK_CURRENT_BAG_OCCUPANCY_SQL))
                .contains(
                        "FROM REC_BAG_CURRENT_OCCUPANCY",
                        "FOR UPDATE")
                .doesNotContain("JOIN REC_BAG");
        assertThat(upper(
                RecyclingStartDeliveryBusinessFactsAdapter.LOAD_BAG_SQL))
                .contains("FROM REC_BAG")
                .doesNotContain("FOR UPDATE");

        assertThat(upper(
                RecyclingStartDeliveryBusinessFactsAdapter
                        .LOCK_DELIVERY_CONFIGURATION_HEAD_SQL))
                .contains(
                        "FROM REC_ORGANIZATION_DELIVERY_CONFIG_HEAD",
                        "FOR UPDATE");
        assertThat(upper(
                RecyclingStartDeliveryBusinessFactsAdapter
                        .LOAD_DELIVERY_CONFIGURATION_SQL))
                .contains("FROM REC_ORGANIZATION_DELIVERY_CONFIG")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    void locksCurrentCapacityAndComparesItWithTheOccupiedBag() {
        assertThat(allStaticSql())
                .contains(
                        "FROM REC_PORT_CAPACITY_STATE",
                        "CURRENT_BAG_ID",
                        "CURRENT_BASELINE_ID",
                        "CURRENT_BASELINE_WEIGHT_G",
                        "CONFIRMED_FULLNESS_STATE",
                        "FROM REC_PORT_WEIGHT_BASELINE",
                        "BAG_ID",
                        "BASELINE_WEIGHT_G",
                        "FOR UPDATE");
    }

    @Test
    void onlyTheMatchingCurrentBagFullStateBlocksDelivery() {
        var currentFull =
                new RecyclingStartDeliveryBusinessFactsAdapter
                        .CapacityAdmissionRow(
                                41L, "VALID", 51L, 100L, "FULL");
        var removedBagFull =
                new RecyclingStartDeliveryBusinessFactsAdapter
                        .CapacityAdmissionRow(
                                40L, "VALID", 51L, 100L, "FULL");
        var currentNotFull =
                new RecyclingStartDeliveryBusinessFactsAdapter
                        .CapacityAdmissionRow(
                                41L, "VALID", 51L, 100L, "NOT_FULL");

        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull(41L, currentFull)).isTrue();
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull(41L, removedBagFull)).isFalse();
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull(41L, currentNotFull)).isFalse();
    }

    private static String allStaticSql() {
        return Arrays.stream(
                        RecyclingStartDeliveryBusinessFactsAdapter.class
                                .getDeclaredFields())
                .filter(field -> Modifier.isStatic(
                        field.getModifiers()))
                .filter(field -> field.getType() == String.class)
                .map(RecyclingStartDeliveryBusinessFactsAdapterSqlTest
                        ::readStaticString)
                .map(RecyclingStartDeliveryBusinessFactsAdapterSqlTest
                        ::upper)
                .reduce("", (left, right) -> left + "\n" + right);
    }

    private static String readStaticString(Field field) {
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
