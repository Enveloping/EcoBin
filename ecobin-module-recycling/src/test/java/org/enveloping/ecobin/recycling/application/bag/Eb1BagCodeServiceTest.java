package org.enveloping.ecobin.recycling.application.bag;

import org.enveloping.ecobin.recycling.infrastructure.bag.BagCodeAuthenticationProperties;
import org.junit.jupiter.api.Test;

import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class Eb1BagCodeServiceTest {

    private static final byte[] KEY = IntStream.range(0, 32)
            .collect(
                    () -> new java.io.ByteArrayOutputStream(32),
                    java.io.ByteArrayOutputStream::write,
                    (left, right) -> left.writeBytes(right.toByteArray()))
            .toByteArray();
    private static final String GOLDEN =
            "EB1_K1_000G40R40M30E209185GR38E1W_"
                    + "GRQ320Z8YDWC8V49M7W0";

    @Test
    void reproducesTheFrozenCrossLanguageGoldenVector() {
        byte[] serial = IntStream.range(0, 16)
                .collect(
                        () -> new java.io.ByteArrayOutputStream(16),
                        java.io.ByteArrayOutputStream::write,
                        (left, right) -> left.writeBytes(
                                right.toByteArray()))
                .toByteArray();

        assertThat(Eb1BagCodeService.issue("K1", serial, KEY).value())
                .isEqualTo(GOLDEN);
    }

    @Test
    void acceptsOnlyAnUntamperedCodeFromAConfiguredKeyVersion() {
        Eb1BagCodeService service = service("K1", KEY);

        assertThat(service.authenticate(GOLDEN))
                .get()
                .extracting(code -> code.value(), code -> code.keyId())
                .containsExactly(GOLDEN, "K1");
        assertThat(service.authenticate("  " + GOLDEN + "  "))
                .isEmpty();
        assertThat(service.authenticate(GOLDEN.substring(0, 53) + "1"))
                .isEmpty();
        assertThat(service.authenticate(GOLDEN.replace("K1", "K2")))
                .isEmpty();
        assertThat(service.authenticate("BAG_new_001")).isEmpty();
    }

    @Test
    void generatedCodesAreUniqueAndSelfAuthenticating() {
        Eb1BagCodeService service = service("K1", KEY);
        Set<String> issued = IntStream.range(0, 100)
                .mapToObj(ignored -> service.issue().value())
                .collect(Collectors.toSet());

        assertThat(issued).hasSize(100);
        assertThat(issued).allSatisfy(code ->
                assertThat(service.authenticate(code)).isPresent());
    }

    @Test
    void refusesWeakOrMissingActiveKeysAtStartup() {
        assertThatThrownBy(() -> service("K1", new byte[16]))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("at least 32 bytes");

        BagCodeAuthenticationProperties properties =
                new BagCodeAuthenticationProperties();
        properties.setActiveKeyId("K2");
        properties.setKeys(new LinkedHashMap<>(java.util.Map.of(
                "K1", Base64.getEncoder().encodeToString(KEY))));
        assertThatThrownBy(() -> new Eb1BagCodeService(properties))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("active bag-code HMAC key");
    }

    private static Eb1BagCodeService service(
            String activeKeyId,
            byte[] key) {
        BagCodeAuthenticationProperties properties =
                new BagCodeAuthenticationProperties();
        properties.setActiveKeyId(activeKeyId);
        properties.setKeys(new LinkedHashMap<>(java.util.Map.of(
                activeKeyId,
                Base64.getEncoder().encodeToString(key))));
        return new Eb1BagCodeService(properties);
    }
}
