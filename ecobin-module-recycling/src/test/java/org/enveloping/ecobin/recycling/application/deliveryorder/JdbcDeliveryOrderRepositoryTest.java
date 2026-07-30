package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
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
}
