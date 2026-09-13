package org.enveloping.ecobin.device.application.target;

import org.springframework.jdbc.core.JdbcTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.Optional;

/** Chooses encoding from authenticated installed software, not from API input or a hash. */
final class McuConfigurationProfileProvider {
    private static final String RECOGNIZED_PROFILES_SQL = """
            SELECT projection.asset_id, declaration.uart_protocol_family
            FROM dev_device_compatibility_projection projection
            JOIN dev_device_software_fact fact
              ON fact.id = projection.latest_software_fact_id
             AND fact.asset_id = projection.asset_id
             AND fact.management_state_sequence = projection.management_state_sequence
            JOIN dev_edge_software_release declaration
              ON declaration.release_uid = fact.active_business_release_uid
             AND declaration.release_sequence = fact.active_business_release_sequence
             AND declaration.version_name = fact.active_business_version_name
             AND declaration.package_sha256 = fact.active_business_package_sha256
            WHERE projection.compatibility_status IN ('BASE_COMPATIBLE', 'FULLY_COMPATIBLE')
              AND fact.uart_state = 'READY'
              AND declaration.uart_protocol_family = fact.uart_protocol_family
              AND (
                (declaration.uart_protocol_family = 'ECOBIN_UART'
                 AND declaration.uart_protocol_major = 2
                 AND fact.uart_protocol_major = 2
                 AND declaration.uart_protocol_minor = fact.uart_protocol_minor)
                OR
                (declaration.uart_protocol_family = 'FIXED_FRAME'
                 AND fact.uart_protocol_major IS NULL
                 AND fact.uart_protocol_minor IS NULL
                 AND declaration.required_fixed_frame_revision = fact.mcu_fixed_frame_revision)
              )
            """;
    static final String RECOGNIZED_PROFILE_SQL = RECOGNIZED_PROFILES_SQL
            + " AND projection.asset_id = ?";

    static final String PROFILE_CHANGE_ASSET_IDS_SQL = """
            SELECT asset.id
            FROM dev_device_asset asset
            JOIN (
            """ + RECOGNIZED_PROFILES_SQL + """
            ) recognized ON recognized.asset_id = asset.id
            JOIN dev_config_version config
              ON config.asset_id = asset.id
             AND config.version_no = (
                SELECT MAX(latest.version_no) FROM dev_config_version latest
                WHERE latest.asset_id = asset.id)
            JOIN dev_config_application application
              ON application.config_version_id = config.id
             AND application.asset_id = asset.id
            JOIN dev_device_command command
              ON command.config_application_id = application.id
             AND command.asset_id = asset.id
             AND command.command_type = 'APPLY_CONFIGURATION'
            WHERE asset.tenant_id IS NOT NULL AND asset.organization_id IS NOT NULL
              AND asset.lifecycle_status = 'NORMAL' AND asset.acceptance_status = 'PASSED'
              AND CASE WHEN recognized.uart_protocol_family = 'ECOBIN_UART'
                    THEN 'UART_V2_SIMPLIFIED' ELSE 'LEGACY_V1' END
                  <> COALESCE(JSON_UNQUOTE(JSON_EXTRACT(command.semantic_payload,
                        '$.payload.mcuConfigurationProfile')), 'LEGACY_V1')
            ORDER BY asset.id
            LIMIT 200
            """;

    private final JdbcTemplate jdbc;
    private final ObjectMapper mapper;

    McuConfigurationProfileProvider(JdbcTemplate jdbc, ObjectMapper mapper) {
        this.jdbc = jdbc;
        this.mapper = mapper;
    }

    Optional<McuConfigurationProfile> recognized(long assetId) {
        return jdbc.query(RECOGNIZED_PROFILE_SQL,
                (rs, ignored) -> "ECOBIN_UART".equals(rs.getString("uart_protocol_family"))
                        ? McuConfigurationProfile.UART_V2_SIMPLIFIED
                        : McuConfigurationProfile.LEGACY_V1,
                assetId).stream().findFirst();
    }

    McuConfigurationProfile forPublication(long assetId) {
        // A temporarily unavailable UART never silently downgrades a frozen native profile.
        return recognized(assetId).orElseGet(() -> latestFrozen(assetId));
    }

    McuConfigurationProfile latestFrozen(long assetId) {
        var payloads = jdbc.query("""
                SELECT command.semantic_payload
                FROM dev_config_version config
                JOIN dev_config_application application
                  ON application.config_version_id = config.id
                 AND application.asset_id = config.asset_id
                JOIN dev_device_command command
                  ON command.config_application_id = application.id
                 AND command.asset_id = config.asset_id
                 AND command.command_type = 'APPLY_CONFIGURATION'
                WHERE config.asset_id = ?
                ORDER BY config.version_no DESC, command.id DESC
                LIMIT 1
                """, (rs, ignored) -> rs.getString(1), assetId);
        if (payloads.isEmpty()) {
            return McuConfigurationProfile.LEGACY_V1;
        }
        JsonNode root = mapper.readTree(payloads.getFirst());
        JsonNode payload = root.path("payload");
        if (!payload.isObject()) {
            throw new IllegalStateException("frozen configuration command payload is missing");
        }
        JsonNode profile = payload.get("mcuConfigurationProfile");
        if (profile == null) {
            return McuConfigurationProfile.LEGACY_V1;
        }
        if (profile.isString() && McuConfigurationProfile.UART_V2_SIMPLIFIED.name().equals(profile.asString())) {
            return McuConfigurationProfile.UART_V2_SIMPLIFIED;
        }
        throw new IllegalStateException("frozen configuration profile is unsupported");
    }
}
