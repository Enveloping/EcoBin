package org.enveloping.ecobin.device.api.persistence;

import org.enveloping.ecobin.device.api.result.DeliveryOrderDeviceFacts;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeliveryOrderDeviceFactsContractTest {

    @Test
    void batchKeysRejectAmbiguousCallerTokensAndRedactRawKeys() {
        var first = new DeliveryOrderDeviceFactsRef.FactKey(
                "row-1",
                101L,
                201L,
                301L,
                401L);
        var duplicate = new DeliveryOrderDeviceFactsRef.FactKey(
                "row-1",
                102L,
                202L,
                302L,
                402L);

        assertThatThrownBy(() ->
                new DeliveryOrderDeviceFactsRef.BatchKeys(
                        11L,
                        12L,
                        List.of(first, duplicate)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("tokens must be unique");
        assertThat(first.toString())
                .isEqualTo(
                        "DeliveryOrderDeviceFactsRef.FactKey[REDACTED]")
                .doesNotContain("101", "201", "301", "401");
    }

    @Test
    void resultIsEitherFullyResolvedOrExplicitlyMissing() {
        var missing = DeliveryOrderDeviceFacts.missing("row-1");
        var resolved = new DeliveryOrderDeviceFacts(
                "row-2",
                UUID.fromString(
                        "10000000-0000-4000-8000-000000000001"),
                UUID.fromString(
                        "20000000-0000-4000-8000-000000000001"),
                "Dp_demo_01",
                2);

        assertThat(missing.resolved()).isFalse();
        assertThat(missing.eventUid()).isNull();
        assertThat(missing.sessionUid()).isNull();
        assertThat(missing.deploymentCode()).isNull();
        assertThat(missing.portNo()).isNull();
        assertThat(resolved.resolved()).isTrue();
        assertThatThrownBy(() -> new DeliveryOrderDeviceFacts(
                "row-3",
                resolved.eventUid(),
                null,
                resolved.deploymentCode(),
                resolved.portNo()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("fully resolved or fully missing");
    }
}
