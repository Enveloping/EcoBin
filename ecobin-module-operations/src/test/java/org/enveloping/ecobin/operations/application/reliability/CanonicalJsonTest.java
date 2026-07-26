package org.enveloping.ecobin.operations.application.reliability;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class CanonicalJsonTest {

    private final CanonicalJson canonicalJson =
            new CanonicalJson(JsonMapper.builder().build());

    @Test
    void highPrecisionFractionDoesNotCollapseIntoInteger() {
        var integer = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":1}");
        var preciseFraction = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":1.0000000000000000001}");

        assertNotEquals(integer.sha256Hex(), preciseFraction.sha256Hex());
        assertEquals(
                "{\"value\":1.0000000000000000001}",
                preciseFraction.json());
    }

    @Test
    void exponentAndPlainNotationOfSameExactNumberShareDigest() {
        assertEquals(
                canonicalJson.canonicalize(
                        "FAKE_EVENT", 1, "{\"value\":1e3}").sha256Hex(),
                canonicalJson.canonicalize(
                        "FAKE_EVENT", 1, "{\"value\":1000}").sha256Hex());
        assertEquals(
                canonicalJson.canonicalize(
                        "FAKE_EVENT", 1, "{\"value\":1e-30}").sha256Hex(),
                canonicalJson.canonicalize(
                        "FAKE_EVENT",
                        1,
                        "{\"value\":0.000000000000000000000000000001}")
                        .sha256Hex());
    }

    @Test
    void integerBeyondSignedLongBoundaryRemainsExact() {
        var boundary = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":9223372036854775808}");
        var next = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":9223372036854775809}");

        assertNotEquals(boundary.sha256Hex(), next.sha256Hex());
        assertEquals("{\"value\":9223372036854775808}", boundary.json());
    }

    @Test
    void extremeExponentsDoNotOverflowOrUnderflow() {
        var tiny = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":1e-10000}");
        var huge = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":1e10000}");
        var zero = canonicalJson.canonicalize(
                "FAKE_EVENT", 1, "{\"value\":0}");

        assertNotEquals(zero.sha256Hex(), tiny.sha256Hex());
        assertNotEquals(zero.sha256Hex(), huge.sha256Hex());
        assertEquals("{\"value\":1e-10000}", tiny.json());
        assertEquals("{\"value\":1e+10000}", huge.json());
    }
}
