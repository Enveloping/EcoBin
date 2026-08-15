package org.enveloping.ecobin.device.application.enrollment;

import org.junit.jupiter.api.Test;
import tools.jackson.databind.json.JsonMapper;

import java.util.HexFormat;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

class DeviceEnrollmentCryptoGoldenVectorTest {

    private static final String IDENTITY_KEY =
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHm1Vi6P5lT5QHix"
                    + "Euipi6eQH4U65pW+1+DjkQutBJZk";

    @Test
    void canonicalTranscriptExactlyMatchesPythonClient() {
        DeviceEnrollmentCrypto.CanonicalEnrollmentRequest request =
                new DeviceEnrollmentCrypto.CanonicalEnrollmentRequest(
                        UUID.fromString(
                                "11111111-1111-4111-8111-111111111111"),
                        UUID.fromString(
                                "22222222-2222-4222-8222-222222222222"),
                        "K1",
                        "SELF_ENROLLMENT",
                        "ECM0-CPV0CWYPXP44QW0W5GH2V0NDM1",
                        IDENTITY_KEY,
                        "NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ=",
                        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOfxYqEL7FWa/qGV"
                                + "5NzoS2lWjV0ssJY+tEbAaF4rF/Lw",
                        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3BQBH4LRxW2Vaq"
                                + "T51z2IWDYaYGBIUl4NCMY43HXdjH");
        byte[] canonical = DeviceEnrollmentCrypto.canonicalRequest(
                JsonMapper.builder().build(), request);
        byte[] nonce = new byte[32];
        for (int index = 0; index < nonce.length; index++) {
            nonce[index] = (byte) index;
        }

        assertEquals(
                "3096a265a3dd4e39fbfbdd524f7e8cd25136a31eb51c652d6fef7187ca7e35e6",
                hex(DeviceEnrollmentCrypto.sha256(canonical)));
        assertEquals(
                "e4a79bc49d8972e040fd6389427d695338e39527d5a049b6120789a693837224",
                hex(DeviceEnrollmentCrypto.sha256(
                        DeviceEnrollmentCrypto.transcript(nonce, canonical))));
    }

    @Test
    void hardwareSerialIsDerivedFromTheIdentityKey() {
        assertEquals(
                "ECM0-CPV0CWYPXP44QW0W5GH2V0NDM1",
                DeviceEnrollmentCrypto.deriveHardwareSn(IDENTITY_KEY));
    }

    private static String hex(byte[] value) {
        return HexFormat.of().formatHex(value);
    }
}
