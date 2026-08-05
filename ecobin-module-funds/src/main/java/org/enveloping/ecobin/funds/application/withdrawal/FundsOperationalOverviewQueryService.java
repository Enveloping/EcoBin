package org.enveloping.ecobin.funds.application.withdrawal;

import org.enveloping.ecobin.funds.api.port.FundsOperationalOverviewQueryPort;
import org.enveloping.ecobin.funds.api.result.FundsOperationalOverview;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;

@Service
public class FundsOperationalOverviewQueryService
        implements FundsOperationalOverviewQueryPort {

    private final JdbcTemplate jdbc;

    public FundsOperationalOverviewQueryService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    @Transactional(readOnly = true, propagation = Propagation.MANDATORY)
    public FundsOperationalOverview query(
            ManagementScopePersistenceRef scope,
            Instant from,
            Instant toExclusive) {
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .FUNDS_OPERATIONAL_OVERVIEW,
                (tenantId, organizations, platformId, staffId) -> queryOwned(
                        tenantId, organizations, from, toExclusive));
    }

    private FundsOperationalOverview queryOwned(
            Long tenantId,
            List<ManagementScopePersistenceRef.OrganizationKey> organizations,
            Instant from,
            Instant toExclusive) {
        if (tenantId == null || organizations.isEmpty()) {
            return new FundsOperationalOverview(java.util.Map.of());
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
        args.add(tenantId);
        args.add(time(from));
        args.add(time(toExclusive));
        args.add(tenantId);
        LinkedHashMap<String, FundsOperationalOverview.Metrics> result =
                new LinkedHashMap<>();
        jdbc.query("WITH selected AS (" + selected + ")" + """
                        SELECT selected.organization_id,
                          COALESCE(SUM(withdrawal.amount_cent), 0)
                              AS succeeded_cent,
                          COALESCE(MAX(account.frozen_withdrawal_cent), 0)
                              AS processing_cent,
                          COALESCE(MAX(account.available_payout_cent), 0)
                              AS available_cent
                        FROM selected
                        LEFT JOIN fund_withdrawal_order withdrawal
                          ON withdrawal.tenant_id = ?
                         AND withdrawal.organization_id =
                             selected.organization_id
                         AND withdrawal.business_state = 'SUCCEEDED'
                         AND withdrawal.channel_terminal_at >= ?
                         AND withdrawal.channel_terminal_at < ?
                        LEFT JOIN fund_organization_payout_account account
                          ON account.tenant_id = ?
                         AND account.organization_id =
                             selected.organization_id
                        GROUP BY selected.organization_id
                        ORDER BY selected.organization_id
                        """,
                (org.springframework.jdbc.core.RowCallbackHandler) rs ->
                        result.put(organizationCodes.get(
                                rs.getLong("organization_id")),
                        new FundsOperationalOverview.Metrics(
                                rs.getLong("succeeded_cent"),
                                rs.getLong("processing_cent"),
                                rs.getLong("available_cent"))),
                args.toArray());
        return new FundsOperationalOverview(result);
    }

    private static String selectedOrganizations(int size) {
        return "SELECT ? AS organization_id"
                + " UNION ALL SELECT ?".repeat(size - 1);
    }

    private static LocalDateTime time(Instant value) {
        return LocalDateTime.ofInstant(value, ZoneOffset.UTC);
    }
}
