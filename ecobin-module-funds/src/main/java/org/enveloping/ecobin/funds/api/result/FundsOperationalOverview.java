package org.enveloping.ecobin.funds.api.result;

import java.util.Map;

public record FundsOperationalOverview(Map<String, Metrics> byOrganization) {
    public FundsOperationalOverview { byOrganization = Map.copyOf(byOrganization); }

    public record Metrics(
            long succeededWithdrawalCent,
            long currentProcessingWithdrawalCent,
            long currentAvailablePayoutCent) { }
}
