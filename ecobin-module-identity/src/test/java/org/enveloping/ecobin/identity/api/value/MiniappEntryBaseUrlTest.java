package org.enveloping.ecobin.identity.api.value;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MiniappEntryBaseUrlTest {

    @Test
    void acceptsHttpsBaseAndAppendsExactlyOneDeviceCode() {
        assertTrue(MiniappEntryBaseUrl.isValid(
                "https://entry.example/device"));
        assertTrue(MiniappEntryBaseUrl.isValid(
                "https://entry.example/device?source=poster"));
        assertEquals(
                "https://entry.example/device?deviceCode=Dv_public-code",
                MiniappEntryBaseUrl.appendDeviceCode(
                        "https://entry.example/device",
                        "Dv_public-code"));
        assertEquals(
                "https://entry.example/device?source=poster&deviceCode=Dv_public-code",
                MiniappEntryBaseUrl.appendDeviceCode(
                        "https://entry.example/device?source=poster",
                        "Dv_public-code"));
        assertEquals(
                "https://www.jinshoubao.com/device-entry/?deviceCode=Dv_public-code",
                MiniappEntryBaseUrl.appendDeviceCode(
                        "https://www.jinshoubao.com/device-entry/",
                        "Dv_public-code"));
    }

    @Test
    void rejectsUnsafeOrAlreadyAttributedBaseUrl() {
        assertFalse(MiniappEntryBaseUrl.isValid("http://entry.example/device"));
        assertFalse(MiniappEntryBaseUrl.isValid("https:///device"));
        assertFalse(MiniappEntryBaseUrl.isValid(
                "https://user:secret@entry.example/device"));
        assertFalse(MiniappEntryBaseUrl.isValid(
                "https://entry.example/device#fragment"));
        assertFalse(MiniappEntryBaseUrl.isValid(
                "https://entry.example/device?deviceCode=old"));
        assertFalse(MiniappEntryBaseUrl.isValid(
                "https://entry.example/device?%64eviceCode=old"));
    }
}
