package org.enveloping.ecobin.operations.application.statistics;

import org.enveloping.ecobin.device.api.port.DeviceOperationalOverviewQueryPort;
import org.enveloping.ecobin.funds.api.port.FundsOperationalOverviewQueryPort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.IdentityOperationalOverviewQueryPort;
import org.enveloping.ecobin.identity.api.port.ManagementScopeAuthorizationPort;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery;
import org.enveloping.ecobin.identity.api.query.ManagementScopeAuthorizationQuery.Channel;
import org.enveloping.ecobin.identity.api.persistence.ManagementScopePersistenceRef;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Cleaning;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Delivery;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.DeploymentAttribution;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Funds;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Metrics;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.OperationalOverview;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.OrganizationOverview;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Period;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.RegistrationAttribution;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Registrations;
import org.enveloping.ecobin.operations.web.v1.OperationalOverviewModels.Scope;
import org.enveloping.ecobin.recycling.api.port.RecyclingOperationalOverviewQueryPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;

@Service
public class OperationalOverviewService {

    private static final ZoneId BUSINESS_ZONE = ZoneId.of("Asia/Shanghai");

    private final JdbcTemplate jdbc;
    private final ManagementScopeAuthorizationPort authorization;
    private final IdentityOperationalOverviewQueryPort identity;
    private final DeviceOperationalOverviewQueryPort device;
    private final RecyclingOperationalOverviewQueryPort recycling;
    private final FundsOperationalOverviewQueryPort funds;

    public OperationalOverviewService(
            JdbcTemplate jdbc,
            ManagementScopeAuthorizationPort authorization,
            IdentityOperationalOverviewQueryPort identity,
            DeviceOperationalOverviewQueryPort device,
            RecyclingOperationalOverviewQueryPort recycling,
            FundsOperationalOverviewQueryPort funds) {
        this.jdbc = jdbc;
        this.authorization = authorization;
        this.identity = identity;
        this.device = device;
        this.recycling = recycling;
        this.funds = funds;
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public OperationalOverview web(
            boolean platform,
            String tenantCode,
            String organizationCode,
            LocalDate from,
            LocalDate toExclusive) {
        return overview(
                Channel.WEB, platform, tenantCode, organizationCode,
                from, toExclusive);
    }

    @Transactional(readOnly = true, isolation = Isolation.REPEATABLE_READ)
    public OperationalOverview miniapp(
            LocalDate from, LocalDate toExclusive) {
        return overview(
                Channel.MINIAPP_STAFF, false, null, null,
                from, toExclusive);
    }

    private OperationalOverview overview(
            Channel channel,
            boolean platform,
            String tenantCode,
            String organizationCode,
            LocalDate from,
            LocalDate toExclusive) {
        validate(from, toExclusive);
        var authorized = authorization.authorize(
                new ManagementScopeAuthorizationQuery(
                        channel, platform, tenantCode, organizationCode,
                        "statistics.read"));
        if (authorized.tenantCode() == null) {
            throw new IllegalStateException(
                    "statistics must resolve a tenant");
        }
        Instant fromInstant = from.atStartOfDay(BUSINESS_ZONE).toInstant();
        Instant toInstant = toExclusive.atStartOfDay(BUSINESS_ZONE).toInstant();
        Instant asOf = databaseNow();
        var identitySnapshot = identity.query(
                authorized.persistenceRef(),
                fromInstant, toInstant);
        var deviceSnapshot = device.query(
                authorized.persistenceRef(), identitySnapshot.organizations());
        var recyclingSnapshot = recycling.query(
                authorized.persistenceRef(),
                fromInstant, toInstant);
        var fundsSnapshot = funds.query(
                authorized.persistenceRef(),
                fromInstant, toInstant);
        Map<String, Long> openAlerts = openAlerts(
                authorized.persistenceRef());
        List<OrganizationOverview> items = identitySnapshot.organizations()
                .stream().map(org -> {
                    var rec = recyclingSnapshot.byOrganization()
                            .get(org.organizationCode());
                    var fund = fundsSnapshot.byOrganization()
                            .get(org.organizationCode());
                    var registrations = deviceSnapshot
                            .registrationAttributions()
                            .getOrDefault(org.organizationCode(), List.of());
                    long attributed = registrations.stream()
                            .mapToLong(value -> value.registeredUserCount()).sum();
                    Metrics metrics = metrics(
                            org.registeredUserCount(),
                            org.directEntryCount(),
                            attributed,
                            rec,
                            deviceSnapshot.onlineByOrganization()
                                    .getOrDefault(org.organizationCode(), 0L),
                            openAlerts.getOrDefault(
                                    org.organizationCode(), 0L),
                            fund);
                    return new OrganizationOverview(
                            org.organizationCode(), org.organizationName(),
                            metrics,
                            new RegistrationAttribution(
                                    org.directEntryCount(),
                                    registrations.stream().map(value ->
                                            new DeploymentAttribution(
                                                    value.deploymentCode(),
                                                    value.deploymentName(),
                                                    value.registeredUserCount()))
                                            .toList()));
                }).toList();
        Metrics totals = sum(items);
        return new OperationalOverview(
                new Period(BUSINESS_ZONE.getId(), from, toExclusive),
                asOf,
                new Scope(
                        authorized.tenantCode(), organizationCode,
                        items.size()),
                totals,
                items);
    }

    private Map<String, Long> openAlerts(
            ManagementScopePersistenceRef scope) {
        return scope.withScopeOnce(
                ManagementScopePersistenceRef.Purpose
                        .OPERATIONS_OPERATIONAL_OVERVIEW,
                (tenantId, organizations, platformId, staffId) -> {
            if (tenantId == null || organizations.isEmpty()) {
                return Map.of();
            }
            String placeholders = "?,".repeat(organizations.size());
            placeholders = placeholders.substring(0, placeholders.length() - 1);
            var args = new java.util.ArrayList<Object>();
            args.add(tenantId);
            args.addAll(organizations.stream()
                    .map(ManagementScopePersistenceRef.OrganizationKey::value)
                    .toList());
            var byId = new java.util.LinkedHashMap<Long, Long>();
            jdbc.query("""
                        SELECT alert.organization_id, COUNT(*) total
                        FROM ops_alert alert
                        WHERE alert.status = 'OPEN'
                          AND alert.tenant_id = ?
                          AND alert.organization_id IN (
                        """ + placeholders + ")" + """
                        GROUP BY alert.organization_id
                        """,
                    (org.springframework.jdbc.core.RowCallbackHandler) rs ->
                        byId.put(
                        rs.getLong("organization_id"),
                        rs.getLong("total")), args.toArray());
            var result = new java.util.LinkedHashMap<String, Long>();
            for (var organization : organizations) {
                result.put(organization.code(),
                        byId.getOrDefault(organization.value(), 0L));
            }
            return result;
        });
    }

    private static Metrics metrics(
            long registered,
            long direct,
            long attributed,
            org.enveloping.ecobin.recycling.api.result
                    .RecyclingOperationalOverview.Metrics recycling,
            long online,
            long openAlerts,
            org.enveloping.ecobin.funds.api.result
                    .FundsOperationalOverview.Metrics funds) {
        var rec = recycling == null
                ? new org.enveloping.ecobin.recycling.api.result
                .RecyclingOperationalOverview.Metrics(
                0, 0, BigDecimal.ZERO, 0, 0, 0, 0, 0)
                : recycling;
        var fund = funds == null
                ? new org.enveloping.ecobin.funds.api.result
                .FundsOperationalOverview.Metrics(0, 0, 0)
                : funds;
        return new Metrics(
                new Registrations(registered, direct, attributed),
                new Delivery(
                        rec.createdOrderCount(), rec.recognizedOrderCount(),
                        decimal(rec.recognizedWeightKg()),
                        yuan(rec.recognizedCashbackCent()),
                        rec.currentPendingReviewCount()),
                new Cleaning(
                        rec.createdCleanRecordCount(),
                        rec.anomalousCleanRecordCount()),
                new org.enveloping.ecobin.operations.web.v1
                        .OperationalOverviewModels.Operations(
                        online, rec.currentFullPortCount(), openAlerts),
                new Funds(
                        yuan(fund.succeededWithdrawalCent()),
                        yuan(fund.currentProcessingWithdrawalCent()),
                        yuan(fund.currentAvailablePayoutCent())));
    }

    private static Metrics sum(List<OrganizationOverview> items) {
        long registered = 0, direct = 0, attributed = 0;
        long created = 0, recognized = 0, pending = 0;
        BigDecimal weight = BigDecimal.ZERO;
        long cashback = 0, cleans = 0, anomalous = 0;
        long online = 0, full = 0, alerts = 0;
        long succeeded = 0, processing = 0, available = 0;
        for (OrganizationOverview item : items) {
            Metrics m = item.metrics();
            registered += m.registrations().registeredUserCount();
            direct += m.registrations().directEntryCount();
            attributed += m.registrations().deviceAttributedCount();
            created += m.delivery().createdOrderCount();
            recognized += m.delivery().recognizedOrderCount();
            weight = weight.add(new BigDecimal(
                    m.delivery().recognizedWeightKg()));
            cashback += cents(m.delivery().recognizedCashbackYuan());
            pending += m.delivery().currentPendingReviewCount();
            cleans += m.cleaning().createdRecordCount();
            anomalous += m.cleaning().anomalousRecordCount();
            online += m.operations().currentOnlineDeploymentCount();
            full += m.operations().currentFullPortCount();
            alerts += m.operations().currentOpenAlertCount();
            succeeded += cents(m.funds().succeededWithdrawalYuan());
            processing += cents(m.funds().currentProcessingWithdrawalYuan());
            available += cents(m.funds().currentAvailablePayoutYuan());
        }
        return new Metrics(
                new Registrations(registered, direct, attributed),
                new Delivery(created, recognized, decimal(weight),
                        yuan(cashback), pending),
                new Cleaning(cleans, anomalous),
                new org.enveloping.ecobin.operations.web.v1
                        .OperationalOverviewModels.Operations(
                        online, full, alerts),
                new Funds(yuan(succeeded), yuan(processing), yuan(available)));
    }

    private Instant databaseNow() {
        LocalDateTime result = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        if (result == null) {
            throw new IllegalStateException("database time unavailable");
        }
        return result.toInstant(ZoneOffset.UTC);
    }

    private static void validate(LocalDate from, LocalDate to) {
        LocalDate latestExclusive = LocalDate.now(BUSINESS_ZONE).plusDays(1);
        if (from == null || to == null || !from.isBefore(to)
                || ChronoUnit.DAYS.between(from, to) > 31
                || from.isAfter(LocalDate.now(BUSINESS_ZONE))
                || to.isAfter(latestExclusive)) {
            throw new TargetApiException(
                    400,
                    from != null && to != null
                            && ChronoUnit.DAYS.between(from, to) > 31
                            ? "STATISTICS.DATE_RANGE_TOO_LARGE"
                            : "STATISTICS.DATE_RANGE_INVALID",
                    "业务日期范围无效");
        }
    }

    private static String decimal(BigDecimal value) {
        return (value == null ? BigDecimal.ZERO : value)
                .setScale(2, java.math.RoundingMode.HALF_UP).toPlainString();
    }

    private static String yuan(long cents) {
        return BigDecimal.valueOf(cents, 2).toPlainString();
    }

    private static long cents(String yuan) {
        return new BigDecimal(yuan).movePointRight(2).longValueExact();
    }

}
