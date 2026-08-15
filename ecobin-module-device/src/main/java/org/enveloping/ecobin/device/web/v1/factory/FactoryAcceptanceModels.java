package org.enveloping.ecobin.device.web.v1.factory;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;

public final class FactoryAcceptanceModels {

    private FactoryAcceptanceModels() {
    }

    public record InstallFactoryBagRequest(
            @NotNull @Min(1) @Max(6) Integer portNo,
            @NotBlank @Size(max = 128) String bagCode) {
    }

    public record CorrectFactoryBagRequest(
            @NotBlank @Size(max = 128) String bagCode,
            @NotBlank @Size(max = 500) String reason) {
    }

    public record FactoryBagSlotView(
            int portNo,
            String bagCode,
            Instant installedAt) {
    }

    public record FactoryAcceptanceView(
            String deviceCode,
            String hardwareSn,
            int expectedPortCount,
            String acceptanceStatus,
            boolean allFactoryBagsInstalled,
            boolean acceptanceCanStart,
            List<FactoryBagSlotView> factoryBags) {

        public FactoryAcceptanceView {
            factoryBags = List.copyOf(factoryBags);
        }
    }
}
