package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeviceEntryUrlFactoryTest {

    @Test
    void buildsTheExactAsciiUrlAndItsSha256() throws Exception {
        DeviceEntryUrlFactory factory = new DeviceEntryUrlFactory(
                "https://www.jinshoubao.com/device-entry/");

        var entry = factory.create("Dv_public-code-1");

        assertThat(entry.url()).isEqualTo(
                "https://www.jinshoubao.com/device-entry/"
                        + "?deviceCode=Dv_public-code-1");
        assertThat(entry.sha256Hex()).isEqualTo(
                HexFormat.of().formatHex(
                        MessageDigest.getInstance("SHA-256").digest(
                                entry.url().getBytes(
                                        StandardCharsets.US_ASCII))));
        assertThat(entry.url().getBytes(StandardCharsets.US_ASCII))
                .hasSizeLessThanOrEqualTo(
                        DeviceEntryUrlFactory.MAXIMUM_ASCII_BYTES);
    }

    @Test
    void rejectsABaseThatCannotFitTheWorstCaseDeviceCode() {
        String oversized = "https://example.com/" + "a".repeat(170) + "/";

        assertThatThrownBy(() -> new DeviceEntryUrlFactory(oversized))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("192-byte");
    }
}
