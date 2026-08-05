package org.enveloping.ecobin.recycling.application.fullness;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class RecyclingOperationalAlertSourceQueryServiceSqlTest {

    @Test
    void ordersCapacityRowsByTheActualPrimaryKey() {
        String sql = RecyclingOperationalAlertSourceQueryService
                .LOAD_PORT_FULLNESS_ALERT_FACTS_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql)
                .contains("order by capacity.port_id")
                .doesNotContain("capacity.id");
    }
}
