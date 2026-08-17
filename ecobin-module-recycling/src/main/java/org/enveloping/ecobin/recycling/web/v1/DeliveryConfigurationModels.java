package org.enveloping.ecobin.recycling.web.v1;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class DeliveryConfigurationModels {

    private DeliveryConfigurationModels() {
    }

    public record DeliveryConfigurationReleaseRequest(
            @NotNull @Min(1) Long expectedLatestVersion,
            @NotBlank String reviewMode,
            String automaticReviewMaxAmountYuan,
            @NotBlank String openBalanceFloorYuan,
            @NotBlank String maxReviewAbsoluteWeightKg,
            @Size(max = 500) String reason) {
    }

    public record DeliveryConfigurationVersion(
            long versionNo,
            String contentSha256,
            String reviewMode,
            String automaticReviewMaxAmountYuan,
            String openBalanceFloorYuan,
            String maxReviewAbsoluteWeightKg,
            String publicationSource,
            UUID publishedByStaffAccountUid,
            String publishedBy,
            Instant publishedAt,
            boolean current) {
    }

    public record DeliveryConfigurationVersionPage(
            List<DeliveryConfigurationVersion> items,
            Long nextBeforeVersionNo) {

        public DeliveryConfigurationVersionPage {
            items = List.copyOf(items);
        }
    }
}
