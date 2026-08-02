package org.enveloping.ecobin.recycling.infrastructure.delivery;

import org.junit.jupiter.api.Test;

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
    void onlyFullFactBoundToCurrentBagBlocksDelivery() {
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull("FULL", 41L, 41L)).isTrue();
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull("FULL", 40L, 41L)).isFalse();
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull("FULL", null, 41L)).isFalse();
        assertThat(RecyclingStartDeliveryBusinessFactsAdapter
                .isCurrentBagFull("NOT_FULL", 41L, 41L)).isFalse();
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
