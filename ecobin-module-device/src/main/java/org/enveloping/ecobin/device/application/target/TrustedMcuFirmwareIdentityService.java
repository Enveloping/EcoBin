package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.regex.Pattern;

/**
 * Registers an MCU identity observed through a successful fixed-frame F3 query.
 *
 * <p>The caller has already authenticated the OneNet device and accepted the
 * runtime event sequence.  This service still validates every F3 success
 * invariant before the observation can unlock revision-2 cloud rollout.</p>
 */
@Service
public class TrustedMcuFirmwareIdentityService {

    private static final Pattern IDENTITY = Pattern.compile(
            "^[0-9a-f]{16}$");

    private final JdbcTemplate jdbc;

    public TrustedMcuFirmwareIdentityService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** Applies an optional trusted identity observation from a runtime fact. */
    public boolean applyTrustedRuntimeObservation(
            long assetId,
            JsonNode runtimePayload,
            LocalDateTime now) {
        JsonNode observation = runtimePayload.get("mcuFirmwareIdentity");
        if (observation == null || observation.isNull()) {
            return false;
        }
        if (!observation.isObject()
                || !"OK".equals(text(observation, "queryStatus"))
                || integer(observation, "statusCode", 0, 0) != 0
                || integer(
                        observation,
                        "fixedFrameRevision",
                        2,
                        2) != 2) {
            throw untrusted("MCU firmware identity is not a successful F3 fact");
        }
        long versionCode = integer(
                observation,
                "firmwareVersionCode",
                1,
                4_294_967_295L);
        String version = text(observation, "firmwareVersion");
        if (version.length() < 5 || version.length() > 32) {
            throw untrusted("MCU firmware version is invalid");
        }
        String identity = text(observation, "firmwareIdentityHex");
        if (!IDENTITY.matcher(identity).matches()) {
            throw untrusted("MCU firmware identity digest is invalid");
        }
        JsonNode runtimeVersionNode = runtimePayload.get(
                "mcuFirmwareVersion");
        if (runtimeVersionNode == null
                || !runtimeVersionNode.isTextual()
                || !version.equals(runtimeVersionNode.asText())) {
            throw untrusted(
                    "MCU firmware identity differs from runtime version");
        }
        int updated = jdbc.update("""
                        UPDATE dev_device_asset
                        SET mcu_firmware_version_code = ?,
                            mcu_firmware_identity_hex = ?,
                            mcu_fixed_frame_revision = 2,
                            updated_at = ?
                        WHERE id = ?
                        """,
                versionCode,
                identity,
                now,
                assetId);
        if (updated != 1) {
            throw new IllegalStateException(
                    "trusted MCU identity asset update lost");
        }
        return true;
    }

    private static String text(JsonNode source, String field) {
        JsonNode value = source.get(field);
        if (value == null || !value.isTextual() || value.asText().isBlank()) {
            throw untrusted("MCU firmware identity field is invalid: " + field);
        }
        return value.asText();
    }

    private static long integer(
            JsonNode source,
            String field,
            long minimum,
            long maximum) {
        JsonNode value = source.get(field);
        if (value == null
                || !value.isIntegralNumber()
                || !value.canConvertToLong()) {
            throw untrusted("MCU firmware identity field is invalid: " + field);
        }
        long result = value.longValue();
        if (result < minimum || result > maximum) {
            throw untrusted("MCU firmware identity field is invalid: " + field);
        }
        return result;
    }

    private static UntrustedInboxSourceException untrusted(String message) {
        return new UntrustedInboxSourceException(message);
    }
}
