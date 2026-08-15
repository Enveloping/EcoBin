package org.enveloping.ecobin.integration.fake;

import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningException;
import org.enveloping.ecobin.device.api.port.OneNetDeviceProvisioningPort;
import org.enveloping.ecobin.device.api.result.OneNetProvisionedDevice;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** In-memory, no-network enrollment adapter for local tests only. */
final class FakeOneNetDeviceProvisioningAdapter
        implements OneNetDeviceProvisioningPort {

    private final Map<String, OneNetProvisionedDevice> devices =
            new ConcurrentHashMap<>();

    @Override
    public OneNetProvisionedDevice ensureDevice(
            String deviceName,
            String descriptionMarker,
            boolean createIfMissing) {
        OneNetProvisionedDevice current = devices.get(deviceName);
        if (current != null) {
            if (createIfMissing
                    && !descriptionMarker.equals(current.description())) {
                throw OneNetDeviceProvisioningException.permanent(
                        "ONENET_DEVICE_OWNERSHIP_CONFLICT",
                        "fake OneNet device marker does not match");
            }
            return current;
        }
        if (!createIfMissing) {
            throw OneNetDeviceProvisioningException.permanent(
                    "ONENET_DEVICE_NOT_FOUND",
                    "fake OneNet device does not exist");
        }
        OneNetProvisionedDevice created = new OneNetProvisionedDevice(
                "fake-" + hexSha256(deviceName).substring(0, 24),
                deviceName,
                descriptionMarker,
                Base64.getUrlEncoder().withoutPadding().encodeToString(
                        sha256(("fake-device-key\0" + deviceName)
                                .getBytes(StandardCharsets.UTF_8))));
        OneNetProvisionedDevice raced = devices.putIfAbsent(
                deviceName, created);
        return raced == null ? created : raced;
    }

    private static String hexSha256(String value) {
        return java.util.HexFormat.of().formatHex(sha256(
                value.getBytes(StandardCharsets.UTF_8)));
    }

    private static byte[] sha256(byte[] value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value);
        } catch (NoSuchAlgorithmException unavailable) {
            throw new IllegalStateException(
                    "SHA-256 is unavailable", unavailable);
        }
    }
}
