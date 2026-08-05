package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.recycling.api.port.RecyclingOperationalOverviewQueryPort;
import org.enveloping.ecobin.recycling.api.result.RecyclingOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;

@Service
public class RecyclingOperationalOverviewQueryService
        implements RecyclingOperationalOverviewQueryPort {

    private final JdbcTemplate jdbc;

    public RecyclingOperationalOverviewQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public RecyclingOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive) {
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .RECYCLING_OPERATIONAL_OVERVIEW,
                (tenantId, organizations, platformId, staffId) -> queryOwned(
                        tenantId, organizations, from, toExclusive));
    }

    private RecyclingOperationalOverview queryOwned(
            Long tenantId,
            List<ManagementScopePersistenceRef.OrganizationKey> organizations,
            Instant from,
            Instant toExclusive) {
        if (tenantId == null || organizations.isEmpty()) {
            return new RecyclingOperationalOverview(java.util.Map.of());
        }
        List<Long> organizationIds = organizations.stream()
                .map(ManagementScopePersistenceRef.OrganizationKey::value)
                .toList();
        java.util.Map<Long, String> organizationCodes = organizations.stream()
                .collect(java.util.stream.Collectors.toMap(
                        ManagementScopePersistenceRef.OrganizationKey::value,
                        ManagementScopePersistenceRef.OrganizationKey::code));
        String selected = selectedOrganizations(organizationIds.size());
        List<Object> args = new ArrayList<>();
        args.addAll(organizationIds);
        for (int index = 0; index < 4; index++) {
            args.add(tenantId);
            args.add(time(from));
            args.add(time(toExclusive));
        }
        args.add(tenantId);
        for (int index = 0; index < 2; index++) {
            args.add(tenantId);
            args.add(time(from));
            args.add(time(toExclusive));
        }
        args.add(tenantId);
        LinkedHashMap<String, RecyclingOperationalOverview.Metrics> result =
                new LinkedHashMap<>();
        jdbc.query("WITH selected AS (" + selected + ")" + """
                        SELECT selected.organization_id,
                          (SELECT COUNT(*) FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = ?
                             AND delivery.organization_id =
                                 selected.organization_id
                             AND delivery.device_occurred_at >= ?
                             AND delivery.device_occurred_at < ?)
                              AS created_order_count,
                          (SELECT COUNT(*) FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = ?
                             AND delivery.organization_id =
                                 selected.organization_id
                             AND delivery.device_occurred_at >= ?
                             AND delivery.device_occurred_at < ?
                             AND delivery.review_status = 'APPROVED')
                              AS recognized_order_count,
                          (SELECT COALESCE(SUM(
                                      delivery.final_business_weight_kg), 0)
                           FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = ?
                             AND delivery.organization_id =
                                 selected.organization_id
                             AND delivery.device_occurred_at >= ?
                             AND delivery.device_occurred_at < ?
                             AND delivery.review_status = 'APPROVED')
                              AS recognized_weight_kg,
                          (SELECT COALESCE(SUM(delivery.final_amount_cent), 0)
                           FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = ?
                             AND delivery.organization_id =
                                 selected.organization_id
                             AND delivery.device_occurred_at >= ?
                             AND delivery.device_occurred_at < ?
                             AND delivery.review_status = 'APPROVED')
                              AS recognized_cashback_cent,
                          (SELECT COUNT(*) FROM rec_delivery_order delivery
                           WHERE delivery.tenant_id = ?
                             AND delivery.organization_id =
                                 selected.organization_id
                             AND delivery.review_status = 'PENDING')
                              AS pending_review_count,
                          (SELECT COUNT(*) FROM rec_clean_record clean
                           WHERE clean.tenant_id = ?
                             AND clean.organization_id =
                                 selected.organization_id
                             AND clean.completed_at >= ?
                             AND clean.completed_at < ?)
                              AS clean_record_count,
                          (SELECT COUNT(*) FROM rec_clean_record clean
                           WHERE clean.tenant_id = ?
                             AND clean.organization_id =
                                 selected.organization_id
                             AND clean.completed_at >= ?
                             AND clean.completed_at < ?
                             AND clean.record_class = 'SYSTEM_ANOMALY')
                              AS anomalous_clean_count,
                          (SELECT COUNT(*) FROM rec_port_capacity_state capacity
                           WHERE capacity.tenant_id = ?
                             AND capacity.organization_id =
                                 selected.organization_id
                             AND capacity.confirmed_fullness_state = 'FULL')
                              AS full_port_count
                        FROM selected
                        ORDER BY selected.organization_id
                        """,
                (org.springframework.jdbc.core.RowCallbackHandler) rs ->
                        result.put(organizationCodes.get(
                                rs.getLong("organization_id")),
                        new RecyclingOperationalOverview.Metrics(
                                rs.getLong("created_order_count"),
                                rs.getLong("recognized_order_count"),
                                rs.getBigDecimal("recognized_weight_kg"),
                                rs.getLong("recognized_cashback_cent"),
                                rs.getLong("pending_review_count"),
                                rs.getLong("clean_record_count"),
                                rs.getLong("anomalous_clean_count"),
                                rs.getLong("full_port_count"))),
                args.toArray());
        return new RecyclingOperationalOverview(result);
    }

    private static String selectedOrganizations(int size) {
        return "SELECT ? AS organization_id"
                + " UNION ALL SELECT ?".repeat(size - 1);
    }

    private static LocalDateTime time(Instant value) {
        return LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }
}
