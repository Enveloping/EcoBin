package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;

import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedDeviceAcceptanceChallengeServiceTest {

    @Test
    void evidenceMustMatchTheExactChallengeBagSnapshot() {
        byte[] digest = HexFormat.of().parseHex("ab".repeat(32));

        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 4L, digest))
                .isTrue();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 5L, digest))
                .isFalse();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(
                        4L, "ab".repeat(32), 4L,
                        HexFormat.of().parseHex("cd".repeat(32))))
                .isFalse();
        assertThat(TrustedDeviceAcceptanceChallengeService
                .sameFactoryBagSnapshot(0L, null, 0L, digest))
                .isFalse();
    }
}
