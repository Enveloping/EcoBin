package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.ArgumentCaptor;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JdbcDeliveryOrderRepositoryTest {

    private static final long TENANT_ID = 46L;
    private static final long ORGANIZATION_ID = 49L;

    @Mock
    private JdbcTemplate jdbc;

    private JdbcDeliveryOrderRepository repository;

    @BeforeEach
    void setUp() {
        repository = new JdbcDeliveryOrderRepository(jdbc);
    }

    @Test
    @SuppressWarnings("unchecked")
    void missingOrganizationCounterStartsAnEmptyQuerySnapshot() {
        when(jdbc.query(
                anyString(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID)))
                .thenReturn(List.of());

        long highWatermark = repository.currentHighWatermark(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null));

        assertThat(highWatermark).isZero();
    }

    @Test
    @SuppressWarnings("unchecked")
    void previewOrderReadDoesNotUseForUpdate() {
        ArgumentCaptor<String> sql =
                ArgumentCaptor.forClass(String.class);
        when(jdbc.query(
                sql.capture(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID),
                eq("DO-PREVIEW")))
                .thenReturn(List.of());

        repository.findOrder(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null),
                "DO-PREVIEW");

        assertThat(sql.getValue())
                .contains("FROM rec_delivery_order")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    @SuppressWarnings("unchecked")
    void detailReadLoadsTheCurrentRevisionReviewerAndReason() {
        ArgumentCaptor<String> sql =
                ArgumentCaptor.forClass(String.class);
        when(jdbc.query(
                sql.capture(),
                any(RowMapper.class),
                eq(TENANT_ID),
                eq(ORGANIZATION_ID),
                eq("DO-DETAIL")))
                .thenReturn(List.of());

        repository.findDetail(
                new DeliveryOrderScope(
                        TENANT_ID,
                        ORGANIZATION_ID,
                        null),
                "DO-DETAIL");

        assertThat(sql.getValue())
                .contains(
                        "current_revision.reviewer_kind",
                        "current_revision.reason",
                        "LEFT JOIN rec_delivery_revision current_revision",
                        "current_revision.id = o.current_revision_id",
                        "current_revision.delivery_order_id = o.id",
                        "current_revision.revision_no =")
                .doesNotContain("FOR UPDATE");
    }
}
