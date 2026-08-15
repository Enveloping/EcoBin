package org.enveloping.ecobin.device.application.factory;

import org.enveloping.ecobin.device.web.v1.factory.FactoryAcceptanceModels.FactoryBagSlotView;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.HexFormat;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class FactoryAcceptancePolicyTest {

    @Test
    void unverifiedPlatformRowsCannotStartAcceptance() {
        FactoryBagSlotView platformRow = slot(
                "PLATFORM_CREATE", false);

        assertThat(platformRow.verificationStatus())
                .isEqualTo("NEEDS_FACTORY_SCAN");
        assertThat(FactoryAcceptanceService.allFactoryBagsVerified(
                1, List.of(platformRow))).isFalse();
    }

    @Test
    void factoryScanAndExplicitHistoricalExemptionAreEligible() {
        assertThat(FactoryAcceptanceService.allFactoryBagsVerified(
                2,
                List.of(
                        slot("FACTORY_MINIAPP", true),
                        slot("LEGACY_GRANDFATHERED", true))))
                .isTrue();
    }

    @Test
    void orderedBagSetFingerprintMatchesDatabaseCanonicalForm() {
        assertThat(HexFormat.of().formatHex(
                FactoryAcceptanceService.factoryBagSetSha256(
                        List.of("1:EB1_A", "2:EB1_B"))))
                .isEqualTo(
                        "0031faf2ef1570b7e4bb1c1b210df6b5"
                                + "4c057ac521b5838ef82814525dcdcb10");
    }

    private static FactoryBagSlotView slot(
            String source,
            boolean verified) {
        return new FactoryBagSlotView(
                1,
                "EB1_A",
                source,
                FactoryAcceptanceService.verificationStatus(
                        source, verified),
                Instant.parse("2026-08-15T12:00:00Z"));
    }
}
