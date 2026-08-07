package org.enveloping.ecobin.operations.web.v1;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

public final class OperationalOverviewModels {

    private OperationalOverviewModels() { }

    public record Period(
            String timeZone,
            LocalDate businessDateFrom,
            LocalDate businessDateToExclusive) { }

    public record Scope(
            String tenantCode,
            String organizationCode,
            int organizationCount) { }

    public record Registrations(
            long registeredUserCount,
            long directEntryCount,
            long deviceAttributedCount) { }

    public record Delivery(
            long createdOrderCount,
            long recognizedOrderCount,
            String recognizedWeightKg,
            String recognizedCashbackYuan,
            long currentPendingReviewCount) { }

    public record Cleaning(
            long createdRecordCount, long anomalousRecordCount) { }

    public record Operations(
            long currentOnlineDeviceCount,
            long currentFullPortCount,
            long currentOpenAlertCount) { }

    public record Funds(
            String succeededWithdrawalYuan,
            String currentProcessingWithdrawalYuan,
            String currentAvailablePayoutYuan) { }

    public record Metrics(
            Registrations registrations,
            Delivery delivery,
            Cleaning cleaning,
            Operations operations,
            Funds funds) { }

    public record AssetAttribution(
            String deviceCode,
            String deviceName,
            long registeredUserCount) { }

    public record RegistrationAttribution(
            long directEntryCount,
            List<AssetAttribution> byAsset) {
        public RegistrationAttribution { byAsset = List.copyOf(byAsset); }
    }

    public record OrganizationOverview(
            String organizationCode,
            String organizationName,
            Metrics metrics,
            RegistrationAttribution registrationAttribution) { }

    public record OperationalOverview(
            Period period,
            Instant asOf,
            Scope scope,
            Metrics totals,
            List<OrganizationOverview> organizations) {
        public OperationalOverview { organizations = List.copyOf(organizations); }
    }
}
