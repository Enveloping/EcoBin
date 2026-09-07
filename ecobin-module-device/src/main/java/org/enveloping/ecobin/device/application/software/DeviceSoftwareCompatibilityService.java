package org.enveloping.ecobin.device.application.software;

import org.enveloping.ecobin.framework.reliability.UntrustedInboxSourceException;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Persists one trusted permanent-management software fact and derives the
 * device's current compatibility from facts already installed on the device.
 *
 * <p>This receive-plane service deliberately has no release-registration or
 * update-command API.  An unknown release remains a valid device fact and is
 * projected as {@code UNKNOWN}; it is never reclassified as a malformed inbox
 * message merely because the control plane has not registered it yet.</p>
 */
@Service
public class DeviceSoftwareCompatibilityService {

    public static final String EVENT_TYPE =
            "DEVICE_SOFTWARE_STATE_REPORTED";

    private static final Pattern UUID_V4 = Pattern.compile(
            "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                    + "-[89ab][0-9a-f]{3}-[0-9a-f]{12}$");
    private static final Pattern SHA256 = Pattern.compile("^[0-9a-f]{64}$");
    private static final Pattern VERSION = Pattern.compile(
            "^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$");
    private static final Pattern BITMAP = Pattern.compile("^[0-9a-f]{16}$");
    private static final Pattern IMAGE_COMMUNICATION_VERSION =
            Pattern.compile("^communication-([0-9]{8}-[0-9]{2,6})$");
    private static final Pattern IMAGE_UPDATER_VERSION =
            Pattern.compile("^updater-([0-9]{8}-[0-9]{2,6})$");
    private static final Set<String> GATES = Set.of(
            "OPEN", "DRAINING", "MAINTENANCE", "LOCKED");
    private static final Set<String> PROCESS_STATES = Set.of(
            "STOPPED", "STARTING", "RUNNING", "FAILED");
    private static final Set<String> UART_STATES = Set.of(
            "DISCONNECTED", "NEGOTIATING", "READY",
            "INCOMPATIBLE", "FAULT");

    /* First permanent-management generation understood by this backend. */
    private static final Protocol MANAGEMENT_TRANSPORT_PROTOCOL =
            new Protocol(1, 0);
    private static final Protocol DEVICE_MAINTENANCE_PROTOCOL =
            new Protocol(1, 0);
    private static final Protocol UPDATER_LOCAL_PROTOCOL =
            new Protocol(1, 0);
    private static final int BACKEND_COMMAND_CONTRACT_VERSION = 2;
    private static final int DEVICE_EVENT_CONTRACT_VERSION = 2;
    private static final int BUSINESS_PACKAGE_FORMAT_VERSION = 1;
    private static final int MCU_PACKAGE_FORMAT_VERSION = 1;

    private final JdbcTemplate jdbc;
    private final NamedParameterJdbcTemplate namedJdbc;
    private final ObjectMapper objectMapper;

    public DeviceSoftwareCompatibilityService(
            JdbcTemplate jdbc,
            ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.namedJdbc = new NamedParameterJdbcTemplate(jdbc);
        this.objectMapper = objectMapper;
    }

    /** Applies a fact after its platform scope and asset target are trusted. */
    @Transactional(propagation = Propagation.MANDATORY)
    public ApplyResult apply(
            long sourceInboxId,
            long assetId,
            JsonNode normalized,
            JsonNode event,
            LocalDateTime receivedAt) {
        Fact fact = parseFact(event);
        lockManagementProfile(assetId);

        ExistingFact existing = existingFact(
                fact.eventUid(), sourceInboxId);
        if (existing != null) {
            requireSameFact(existing, assetId, sourceInboxId, fact);
            return new ApplyResult(false, false);
        }
        ExistingFact sequenceFact = sequenceFact(
                assetId, fact.managementStateSequence());
        if (sequenceFact != null) {
            if (!sequenceFact.payloadSha256().equals(fact.payloadSha256())) {
                throw untrusted(
                        "one management state sequence carries conflicting facts");
            }
            return new ApplyResult(false, false);
        }

        long factId;
        try {
            factId = insertFact(
                    sourceInboxId,
                    assetId,
                    normalized,
                    fact,
                    receivedAt);
        } catch (DuplicateKeyException duplicate) {
            ExistingFact concurrent = existingFact(
                    fact.eventUid(), sourceInboxId);
            if (concurrent != null) {
                requireSameFact(
                        concurrent,
                        assetId,
                        sourceInboxId,
                        fact);
                return new ApplyResult(false, false);
            }
            ExistingFact concurrentSequence = sequenceFact(
                    assetId, fact.managementStateSequence());
            if (concurrentSequence != null
                    && concurrentSequence.payloadSha256().equals(
                    fact.payloadSha256())) {
                return new ApplyResult(false, false);
            }
            throw untrusted(
                    "software fact uniqueness conflicts with persisted content");
        }
        transitionToPermanentManagement(
                assetId, fact.eventUid(), receivedAt);

        ProjectionHead projection = lockProjection(assetId);
        boolean advances = projection.managementStateSequence() == null
                || fact.managementStateSequence()
                > projection.managementStateSequence();
        if (!advances) {
            return new ApplyResult(true, false);
        }

        Compatibility compatibility = retainActiveUpdateAdmission(
                assetId, assess(fact));
        int updated = namedJdbc.update("""
                UPDATE dev_device_compatibility_projection
                SET architecture_generation = 'PERMANENT_V1',
                    latest_software_fact_id = :factId,
                    source_event_uid = :eventUid,
                    management_state_sequence = :stateSequence,
                    compatibility_status = :compatibility,
                    business_admission_status = :admission,
                    primary_reason_code = :primaryReasonCode,
                    primary_reason_message = :primaryReasonMessage,
                    reasons_json = :reasonsJson,
                    capabilities_json = :capabilitiesJson,
                    observed_at = :observedAt,
                    received_at = :receivedAt,
                    lock_version = lock_version + 1,
                    updated_at = :receivedAt
                WHERE asset_id = :assetId
                  AND (
                    management_state_sequence IS NULL
                    OR management_state_sequence < :stateSequence
                  )
                """, new MapSqlParameterSource()
                .addValue("factId", factId)
                .addValue("eventUid", fact.eventUid())
                .addValue("stateSequence", fact.managementStateSequence())
                .addValue("compatibility", compatibility.status())
                .addValue("admission", compatibility.admission())
                .addValue("primaryReasonCode",
                        compatibility.primaryReasonCode())
                .addValue("primaryReasonMessage",
                        compatibility.primaryReasonMessage())
                .addValue("reasonsJson", reasonsJson(
                        compatibility.reasons()))
                .addValue("capabilitiesJson", capabilitiesJson(
                        compatibility.capabilities()))
                .addValue("observedAt", fact.observedAt())
                .addValue("receivedAt", receivedAt)
                .addValue("assetId", assetId));
        if (updated != 1) {
            throw new IllegalStateException(
                    "device compatibility projection update lost");
        }
        return new ApplyResult(true, true);
    }

    /**
     * Reassesses the current projection from the latest persisted device fact.
     *
     * <p>Rollout progress and software-state facts are independent trusted
     * messages.  A fact received while an update is active deliberately keeps
     * admission paused.  Once the deployment reaches a terminal state, the
     * control plane calls this method in the same transaction so that the
     * temporary update hold is removed without inventing a new device fact or
     * blindly reopening a device whose latest actual state is not healthy.</p>
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public void reassessLatestFact(long assetId) {
        LocalDateTime now = jdbc.queryForObject(
                "SELECT UTC_TIMESTAMP(3)", LocalDateTime.class);
        reassessLatestFact(assetId, now);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void reassessLatestFact(
            long assetId,
            LocalDateTime reassessedAt) {
        lockManagementProfile(assetId);
        ProjectionHead projection = lockProjection(assetId);
        if (projection.latestSoftwareFactId() == null) {
            throw new IllegalStateException(
                    "device compatibility projection has no software fact");
        }
        List<String> normalizedFacts = jdbc.queryForList("""
                SELECT normalized_payload
                FROM dev_device_software_fact
                WHERE id = ? AND asset_id = ?
                """, String.class, projection.latestSoftwareFactId(), assetId);
        if (normalizedFacts.size() != 1) {
            throw new IllegalStateException(
                    "latest device software fact is unavailable");
        }
        JsonNode normalized = objectMapper.readTree(
                normalizedFacts.getFirst());
        JsonNode event = normalized.has("event")
                ? object(normalized, "event") : normalized;
        Fact fact = parseFact(event);
        if (projection.managementStateSequence() == null
                || fact.managementStateSequence()
                != projection.managementStateSequence()) {
            throw new IllegalStateException(
                    "device compatibility projection fact identity changed");
        }
        Compatibility compatibility = retainActiveUpdateAdmission(
                assetId, assess(fact));
        int updated = namedJdbc.update("""
                UPDATE dev_device_compatibility_projection
                SET compatibility_status = :compatibility,
                    business_admission_status = :admission,
                    primary_reason_code = :primaryReasonCode,
                    primary_reason_message = :primaryReasonMessage,
                    reasons_json = :reasonsJson,
                    capabilities_json = :capabilitiesJson,
                    lock_version = lock_version + 1,
                    updated_at = :reassessedAt
                WHERE asset_id = :assetId
                  AND latest_software_fact_id = :factId
                  AND management_state_sequence = :stateSequence
                """, new MapSqlParameterSource()
                .addValue("compatibility", compatibility.status())
                .addValue("admission", compatibility.admission())
                .addValue("primaryReasonCode",
                        compatibility.primaryReasonCode())
                .addValue("primaryReasonMessage",
                        compatibility.primaryReasonMessage())
                .addValue("reasonsJson", reasonsJson(
                        compatibility.reasons()))
                .addValue("capabilitiesJson", capabilitiesJson(
                        compatibility.capabilities()))
                .addValue("reassessedAt", reassessedAt)
                .addValue("assetId", assetId)
                .addValue("factId", projection.latestSoftwareFactId())
                .addValue("stateSequence",
                        projection.managementStateSequence()));
        if (updated != 1) {
            throw new IllegalStateException(
                    "device compatibility reassessment lost its projection lock");
        }
    }

    private Compatibility retainActiveUpdateAdmission(
            long assetId,
            Compatibility assessed) {
        Integer activeUpdates = jdbc.queryForObject("""
                SELECT COUNT(*)
                FROM dev_edge_software_deployment
                WHERE asset_id = ?
                  AND deployment_status NOT IN (
                      'PLANNED', 'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED',
                      'REJECTED', 'FAILED_LOCKED', 'CANCELLED'
                  )
                """, Integer.class, assetId);
        if (activeUpdates == null || activeUpdates == 0) {
            return assessed;
        }
        List<Reason> reasons = new ArrayList<>(assessed.reasons());
        reasons.addFirst(reason(
                "BUSINESS_RUNTIME_UPDATE_ACTIVE",
                "业务程序正在更新",
                "设备已经进入业务程序更新流程；在成功、恢复上一版本或安全终止前，后台暂停新的投递和清运。",
                Severity.NONE,
                true));
        return new Compatibility(
                assessed.status(),
                "PAUSED",
                List.copyOf(reasons),
                assessed.capabilities());
    }

    private void lockManagementProfile(long assetId) {
        List<String> rows = jdbc.query("""
                        SELECT architecture_generation
                        FROM dev_device_management_profile
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> rs.getString(
                        "architecture_generation"),
                assetId);
        if (rows.size() != 1
                || !Set.of("LEGACY_DIRECT", "PERMANENT_V1")
                .contains(rows.getFirst())) {
            throw new IllegalStateException(
                    "device management profile is unavailable");
        }
    }

    private ExistingFact existingFact(
            String eventUid,
            long sourceInboxId) {
        List<ExistingFact> rows = jdbc.query("""
                        SELECT event_uid, source_inbox_id, asset_id,
                               management_state_sequence,
                               payload_sha256
                        FROM dev_device_software_fact
                        WHERE event_uid = ? OR source_inbox_id = ?
                        """,
                existingFactMapper(),
                eventUid,
                sourceInboxId);
        if (rows.size() > 1) {
            throw untrusted(
                    "software fact identity maps to multiple persisted facts");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private ExistingFact sequenceFact(long assetId, long sequence) {
        List<ExistingFact> rows = jdbc.query("""
                        SELECT event_uid, source_inbox_id, asset_id,
                               management_state_sequence,
                               payload_sha256
                        FROM dev_device_software_fact
                        WHERE asset_id = ?
                          AND management_state_sequence = ?
                        """,
                existingFactMapper(),
                assetId,
                sequence);
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "management state sequence uniqueness is broken");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private static RowMapper<ExistingFact> existingFactMapper() {
        return (rs, ignored) -> new ExistingFact(
                rs.getString("event_uid"),
                rs.getLong("source_inbox_id"),
                rs.getLong("asset_id"),
                rs.getLong("management_state_sequence"),
                HexFormat.of().formatHex(rs.getBytes("payload_sha256")));
    }

    private static void requireSameFact(
            ExistingFact existing,
            long assetId,
            long sourceInboxId,
            Fact fact) {
        if (!existing.eventUid().equals(fact.eventUid())) {
            if (existing.sourceInboxId() == sourceInboxId) {
                throw untrusted(
                        "one inbox identity carries multiple software events");
            }
            throw untrusted(
                    "software fact lookup returned an unrelated identity");
        }
        if (existing.assetId() != assetId
                || existing.managementStateSequence()
                != fact.managementStateSequence()
                || !existing.payloadSha256().equals(fact.payloadSha256())) {
            throw untrusted(
                    "software fact stable identity was reused with different content");
        }
    }

    private long insertFact(
            long sourceInboxId,
            long assetId,
            JsonNode normalized,
            Fact fact,
            LocalDateTime receivedAt) {
        MapSqlParameterSource parameters = new MapSqlParameterSource()
                .addValue("eventUid", fact.eventUid())
                .addValue("sourceInboxId", sourceInboxId)
                .addValue("assetId", assetId)
                .addValue("stateSequence", fact.managementStateSequence())
                .addValue("gate", fact.businessGateState())
                .addValue("agentVersion", fact.agent().version())
                .addValue("managementMajor",
                        fact.agent().managementTransport().major())
                .addValue("managementMinor",
                        fact.agent().managementTransport().minor())
                .addValue("agentBusinessMajor",
                        fact.agent().businessLocal().major())
                .addValue("agentBusinessMinor",
                        fact.agent().businessLocal().minor())
                .addValue("agentUpdaterMajor",
                        fact.agent().updaterLocal().major())
                .addValue("agentUpdaterMinor",
                        fact.agent().updaterLocal().minor())
                .addValue("updaterVersion", fact.updater().version())
                .addValue("maintenanceMajor",
                        fact.updater().deviceMaintenance().major())
                .addValue("maintenanceMinor",
                        fact.updater().deviceMaintenance().minor())
                .addValue("updaterBusinessMajor",
                        fact.updater().businessLocal().major())
                .addValue("updaterBusinessMinor",
                        fact.updater().businessLocal().minor())
                .addValue("businessPackageFormat",
                        fact.updater().businessPackageFormatVersion())
                .addValue("mcuPackageFormat",
                        fact.updater().mcuPackageFormatVersion())
                .addValue("releaseUid", nullableRelease(
                        fact, ActiveRelease::uid))
                .addValue("releaseSequence", nullableRelease(
                        fact, ActiveRelease::sequence))
                .addValue("releaseVersion", nullableRelease(
                        fact, ActiveRelease::version))
                .addValue("releaseSha", fact.activeRelease() == null
                        ? null : HexFormat.of().parseHex(
                        fact.activeRelease().packageSha256()))
                .addValue("processState", fact.businessProcessState())
                .addValue("businessReady", fact.businessReady())
                .addValue("negotiatedAgentBusinessMajor",
                        nullableMajor(fact.agentBusiness()))
                .addValue("negotiatedAgentBusinessMinor",
                        nullableMinor(fact.agentBusiness()))
                .addValue("negotiatedAgentUpdaterMajor",
                        nullableMajor(fact.agentUpdater()))
                .addValue("negotiatedAgentUpdaterMinor",
                        nullableMinor(fact.agentUpdater()))
                .addValue("negotiatedUpdaterBusinessMajor",
                        nullableMajor(fact.updaterBusiness()))
                .addValue("negotiatedUpdaterBusinessMinor",
                        nullableMinor(fact.updaterBusiness()))
                .addValue("mcuVersion", nullableMcu(
                        fact, McuFirmware::version))
                .addValue("mcuVersionCode", nullableMcu(
                        fact, McuFirmware::versionCode))
                .addValue("mcuIdentity", nullableMcu(
                        fact, McuFirmware::identityHex))
                .addValue("fixedFrameRevision", nullableMcu(
                        fact, McuFirmware::fixedFrameRevision))
                .addValue("uartState", fact.uartState())
                .addValue("uartFamily", uartFamily(fact))
                .addValue("uartMajor", nullableMajor(fact.uartProtocol()))
                .addValue("uartMinor", nullableMinor(fact.uartProtocol()))
                .addValue("capabilityBitmap", fact.capabilityBitmapHex())
                .addValue("payloadSha", HexFormat.of().parseHex(
                        fact.payloadSha256()))
                .addValue("normalizedPayload",
                        objectMapper.writeValueAsString(normalized))
                .addValue("observedAt", fact.observedAt())
                .addValue("receivedAt", receivedAt);
        KeyHolder keyHolder = new GeneratedKeyHolder();
        int inserted = namedJdbc.update("""
                INSERT INTO dev_device_software_fact (
                    event_uid, source_inbox_id, asset_id,
                    management_state_sequence, architecture_generation,
                    business_gate_state,
                    communication_agent_version,
                    management_transport_protocol_major,
                    management_transport_protocol_minor,
                    communication_business_protocol_major,
                    communication_business_protocol_minor,
                    communication_updater_protocol_major,
                    communication_updater_protocol_minor,
                    device_updater_version,
                    device_maintenance_protocol_major,
                    device_maintenance_protocol_minor,
                    updater_business_protocol_major,
                    updater_business_protocol_minor,
                    business_package_format_version,
                    mcu_package_format_version,
                    active_business_release_uid,
                    active_business_release_sequence,
                    active_business_version_name,
                    active_business_package_sha256,
                    business_process_state, business_process_ready,
                    negotiated_communication_business_major,
                    negotiated_communication_business_minor,
                    negotiated_communication_updater_major,
                    negotiated_communication_updater_minor,
                    negotiated_updater_business_major,
                    negotiated_updater_business_minor,
                    mcu_firmware_version, mcu_firmware_version_code,
                    mcu_firmware_identity_hex, mcu_fixed_frame_revision,
                    uart_state, uart_protocol_family,
                    uart_protocol_major, uart_protocol_minor,
                    capability_bitmap_hex, payload_sha256,
                    normalized_payload, observed_at,
                    received_at, created_at
                ) VALUES (
                    :eventUid, :sourceInboxId, :assetId,
                    :stateSequence, 'PERMANENT_V1', :gate,
                    :agentVersion, :managementMajor, :managementMinor,
                    :agentBusinessMajor, :agentBusinessMinor,
                    :agentUpdaterMajor, :agentUpdaterMinor,
                    :updaterVersion, :maintenanceMajor, :maintenanceMinor,
                    :updaterBusinessMajor, :updaterBusinessMinor,
                    :businessPackageFormat, :mcuPackageFormat,
                    :releaseUid, :releaseSequence, :releaseVersion, :releaseSha,
                    :processState, :businessReady,
                    :negotiatedAgentBusinessMajor,
                    :negotiatedAgentBusinessMinor,
                    :negotiatedAgentUpdaterMajor,
                    :negotiatedAgentUpdaterMinor,
                    :negotiatedUpdaterBusinessMajor,
                    :negotiatedUpdaterBusinessMinor,
                    :mcuVersion, :mcuVersionCode,
                    :mcuIdentity, :fixedFrameRevision,
                    :uartState, :uartFamily, :uartMajor, :uartMinor,
                    :capabilityBitmap, :payloadSha, :normalizedPayload,
                    :observedAt, :receivedAt, :receivedAt
                )
                """, parameters, keyHolder, new String[]{"id"});
        if (inserted != 1 || keyHolder.getKey() == null) {
            throw new IllegalStateException(
                    "software fact generated identity is unavailable");
        }
        return keyHolder.getKey().longValue();
    }

    private void transitionToPermanentManagement(
            long assetId,
            String eventUid,
            LocalDateTime now) {
        int updated = jdbc.update("""
                        UPDATE dev_device_management_profile
                        SET architecture_generation = 'PERMANENT_V1',
                            transition_source_event_uid = ?,
                            transitioned_at = ?,
                            lock_version = lock_version + 1,
                            updated_at = ?
                        WHERE asset_id = ?
                          AND architecture_generation = 'LEGACY_DIRECT'
                        """,
                eventUid,
                now,
                now,
                assetId);
        if (updated > 1) {
            throw new IllegalStateException(
                    "multiple device management profiles were updated");
        }
    }

    private ProjectionHead lockProjection(long assetId) {
        List<ProjectionHead> rows = jdbc.query("""
                        SELECT architecture_generation,
                               management_state_sequence,
                               latest_software_fact_id
                        FROM dev_device_compatibility_projection
                        WHERE asset_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new ProjectionHead(
                        rs.getString("architecture_generation"),
                        nullableProjectionSequence(rs.getObject(
                                "management_state_sequence")),
                        nullableProjectionSequence(rs.getObject(
                                "latest_software_fact_id"))),
                assetId);
        if (rows.size() != 1
                || !Set.of("LEGACY_DIRECT", "PERMANENT_V1")
                .contains(rows.getFirst().architectureGeneration())) {
            throw new IllegalStateException(
                    "device compatibility projection is unavailable");
        }
        return rows.getFirst();
    }

    static Long nullableProjectionSequence(Object value) {
        if (value == null) {
            return null;
        }
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(
                    "device management sequence has an invalid JDBC type");
        }
        try {
            return new BigDecimal(number.toString()).longValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalStateException(
                    "device management sequence is outside the supported range",
                    error);
        }
    }

    static Integer nullableJdbcInteger(Object value) {
        if (value == null) {
            return null;
        }
        if (!(value instanceof Number number)) {
            throw new IllegalStateException(
                    "persisted integer has an invalid JDBC type");
        }
        try {
            return new BigDecimal(number.toString()).intValueExact();
        } catch (ArithmeticException | NumberFormatException error) {
            throw new IllegalStateException(
                    "persisted integer is outside the supported range",
                    error);
        }
    }

    private Compatibility assess(Fact fact) {
        List<Reason> coreReasons = new ArrayList<>();
        List<Reason> optionalReasons = new ArrayList<>();
        Release release = release(fact.activeRelease());
        boolean imageBridge = imageBridge(fact);

        if (imageBridge) {
            coreReasons.add(reason(
                    "IMAGE_BRIDGE_BASELINE",
                    "当前运行镜像内置业务程序",
                    "设备尚未安装独立业务发布；首次更新会保留镜像内置程序作为失败回滚基线。",
                    Severity.NONE,
                    false));
        } else if (fact.activeRelease() == null) {
            coreReasons.add(reason(
                    "ACTIVE_BUSINESS_RELEASE_NOT_REPORTED",
                    "未上报当前业务程序",
                    "设备没有上报当前正在运行的业务程序发布身份，平台暂时无法确认兼容性。",
                    Severity.UNKNOWN,
                    true));
        } else if (release == null) {
            coreReasons.add(reason(
                    "BUSINESS_RELEASE_NOT_REGISTERED",
                    "当前业务程序尚未登记",
                    "设备已经上报业务程序身份，但该发布尚未在平台登记，平台暂时无法确认兼容性。",
                    Severity.UNKNOWN,
                    true));
        } else {
            checkReleaseIdentity(fact.activeRelease(), release, coreReasons);
            checkReleaseContracts(release, coreReasons);
        }

        checkPermanentProtocols(fact, coreReasons);
        checkNegotiatedProtocols(fact, release, coreReasons);
        if (release != null) {
            checkUart(fact, release, coreReasons);
            if ("READY".equals(fact.uartState())) {
                checkMcuCapabilities(fact, release, coreReasons);
            }
        } else if (imageBridge) {
            checkImageBridgeUart(fact, coreReasons);
        }

        Severity coreSeverity = strongest(coreReasons);
        coreReasons.sort((left, right) -> Integer.compare(
                right.severity().ordinal(),
                left.severity().ordinal()));
        Boolean coreBusiness = switch (coreSeverity) {
            case INCOMPATIBLE -> false;
            case UNKNOWN -> null;
            case OPTIONAL, NONE -> true;
        };
        Boolean businessUpdate = null;
        if (release != null) {
            businessUpdate = fact.updater().businessPackageFormatVersion()
                    == release.packageFormatVersion();
        } else if (imageBridge) {
            businessUpdate = fact.updater().businessPackageFormatVersion()
                    == BUSINESS_PACKAGE_FORMAT_VERSION;
        }
        boolean mcuUpdate = fact.updater().mcuPackageFormatVersion()
                == MCU_PACKAGE_FORMAT_VERSION;

        String status;
        if (coreSeverity == Severity.INCOMPATIBLE) {
            status = "INCOMPATIBLE";
        } else if (coreSeverity == Severity.UNKNOWN) {
            status = "UNKNOWN";
        } else {
            if (!Boolean.TRUE.equals(businessUpdate)) {
                optionalReasons.add(reason(
                        "BUSINESS_PACKAGE_FORMAT_UNSUPPORTED",
                        "暂不支持远程更新业务程序",
                        "设备更新器不支持当前业务发布包格式；投递和清运仍可继续使用。",
                        Severity.OPTIONAL,
                        false));
            }
            if (!mcuUpdate) {
                optionalReasons.add(reason(
                        "MCU_PACKAGE_FORMAT_UNSUPPORTED",
                        "暂不支持远程更新单片机",
                        "设备更新器不支持平台当前的单片机固件包格式；普通投递和清运不受影响。",
                        Severity.OPTIONAL,
                        false));
            }
            status = optionalReasons.isEmpty()
                    ? "FULLY_COMPATIBLE" : "BASE_COMPATIBLE";
        }

        List<Reason> reasons = new ArrayList<>(coreReasons);
        reasons.addAll(optionalReasons);
        String admission;
        if (!"OPEN".equals(fact.businessGateState())) {
            admission = "PAUSED";
            reasons.addFirst(gateReason(fact.businessGateState()));
        } else if ("UNKNOWN".equals(status)) {
            admission = "UNKNOWN";
        } else if ("INCOMPATIBLE".equals(status)) {
            admission = "PAUSED";
        } else if (!fact.businessReady()
                || !"RUNNING".equals(fact.businessProcessState())) {
            admission = "PAUSED";
            reasons.addFirst(reason(
                    "BUSINESS_PROCESS_NOT_READY",
                    "业务程序尚未就绪",
                    "设备业务程序当前没有达到可接收新作业的运行状态，请等待设备恢复后重试。",
                    Severity.NONE,
                    true));
        } else {
            admission = "ACCEPTING";
        }

        return new Compatibility(
                status,
                admission,
                List.copyOf(reasons),
                new Capabilities(coreBusiness, businessUpdate, mcuUpdate));
    }

    private Release release(ActiveRelease active) {
        if (active == null) {
            return null;
        }
        List<Release> rows = jdbc.query("""
                        SELECT release_uid, version_name, release_sequence,
                               package_sha256,
                               package_format_version,
                               backend_command_contract_version,
                               device_event_contract_version,
                               communication_business_protocol_major,
                               communication_business_protocol_minor,
                               updater_business_protocol_major,
                               updater_business_protocol_minor,
                               uart_protocol_family,
                               uart_protocol_major, uart_protocol_minor,
                               required_fixed_frame_revision,
                               required_mcu_capability_bitmap_hex
                        FROM dev_edge_software_release
                        WHERE release_uid = ?
                        """,
                (rs, ignored) -> new Release(
                        rs.getString("release_uid"),
                        rs.getString("version_name"),
                        rs.getLong("release_sequence"),
                        HexFormat.of().formatHex(
                                rs.getBytes("package_sha256")),
                        rs.getInt("package_format_version"),
                        rs.getInt("backend_command_contract_version"),
                        rs.getInt("device_event_contract_version"),
                        new Protocol(
                                rs.getInt(
                                        "communication_business_protocol_major"),
                                rs.getInt(
                                        "communication_business_protocol_minor")),
                        new Protocol(
                                rs.getInt("updater_business_protocol_major"),
                                rs.getInt("updater_business_protocol_minor")),
                        rs.getString("uart_protocol_family"),
                        nullableProtocol(
                                nullableJdbcInteger(rs.getObject(
                                        "uart_protocol_major")),
                                nullableJdbcInteger(rs.getObject(
                                        "uart_protocol_minor"))),
                        nullableJdbcInteger(rs.getObject(
                                "required_fixed_frame_revision")),
                        rs.getString(
                                "required_mcu_capability_bitmap_hex")),
                active.uid());
        if (rows.size() > 1) {
            throw new IllegalStateException(
                    "edge software release identity is not unique");
        }
        return rows.isEmpty() ? null : rows.getFirst();
    }

    private static void checkReleaseIdentity(
            ActiveRelease active,
            Release release,
            List<Reason> reasons) {
        if (!active.uid().equals(release.uid())
                || active.sequence() != release.sequence()
                || !active.version().equals(release.version())
                || !active.packageSha256().equals(release.packageSha256())) {
            reasons.add(reason(
                    "BUSINESS_RELEASE_IDENTITY_MISMATCH",
                    "业务程序身份与发布记录不一致",
                    "设备上报的发布编号、顺序、版本或制品摘要与平台冻结记录不一致，已暂停新的物理业务。",
                    Severity.INCOMPATIBLE,
                    true));
        }
    }

    private static void checkReleaseContracts(
            Release release,
            List<Reason> reasons) {
        if (release.backendCommandContractVersion()
                != BACKEND_COMMAND_CONTRACT_VERSION) {
            reasons.add(reason(
                    "BACKEND_COMMAND_CONTRACT_MISMATCH",
                    "业务命令格式不兼容",
                    "当前业务程序不能可靠接收平台使用的业务命令格式，已暂停新的物理业务。",
                    Severity.INCOMPATIBLE,
                    true));
        }
        if (release.deviceEventContractVersion()
                != DEVICE_EVENT_CONTRACT_VERSION) {
            reasons.add(reason(
                    "DEVICE_EVENT_CONTRACT_MISMATCH",
                    "设备事件格式不兼容",
                    "当前业务程序上报的业务事件格式不在平台支持范围内，已暂停新的物理业务。",
                    Severity.INCOMPATIBLE,
                    true));
        }
    }

    private static void checkPermanentProtocols(
            Fact fact,
            List<Reason> reasons) {
        mismatch(
                fact.agent().managementTransport(),
                MANAGEMENT_TRANSPORT_PROTOCOL,
                "MANAGEMENT_TRANSPORT_PROTOCOL_MISMATCH",
                "设备管理通信格式不兼容",
                "常驻通信代理使用的管理通信格式不受当前平台支持，已暂停新的物理业务。",
                reasons);
        mismatch(
                fact.updater().deviceMaintenance(),
                DEVICE_MAINTENANCE_PROTOCOL,
                "DEVICE_MAINTENANCE_PROTOCOL_MISMATCH",
                "设备维护通信格式不兼容",
                "设备更新器使用的维护通信格式不受当前平台支持，已暂停新的物理业务。",
                reasons);
    }

    private static void checkNegotiatedProtocols(
            Fact fact,
            Release release,
            List<Reason> reasons) {
        if (fact.agentBusiness() == null) {
            reasons.add(notNegotiated(
                    "AGENT_BUSINESS_PROTOCOL_NOT_NEGOTIATED",
                    "通信代理尚未连接业务程序",
                    "常驻通信代理与业务程序尚未完成本机通信协商，暂时无法确认设备可开展业务。"));
        } else {
            boolean mismatch = !fact.agentBusiness().equals(
                    fact.agent().businessLocal())
                    || (release != null && !fact.agentBusiness().equals(
                    release.communicationBusiness()));
            if (mismatch) {
                reasons.add(protocolMismatch(
                        "AGENT_BUSINESS_PROTOCOL_MISMATCH",
                        "通信代理与业务程序不兼容",
                        "常驻通信代理与业务程序协商出的本机通信版本不在双方声明的兼容范围内。"));
            }
        }
        if (fact.agentUpdater() == null) {
            reasons.add(notNegotiated(
                    "AGENT_UPDATER_PROTOCOL_NOT_NEGOTIATED",
                    "通信代理尚未连接设备更新器",
                    "常驻通信代理与设备更新器尚未完成本机通信协商，暂时无法确认设备管理能力。"));
        } else if (!fact.agentUpdater().equals(
                fact.agent().updaterLocal())
                || !fact.agentUpdater().equals(UPDATER_LOCAL_PROTOCOL)) {
            reasons.add(protocolMismatch(
                    "AGENT_UPDATER_PROTOCOL_MISMATCH",
                    "通信代理与设备更新器不兼容",
                    "常驻通信代理与设备更新器协商出的本机通信版本不在双方支持范围内。"));
        }
        if (fact.updaterBusiness() == null) {
            reasons.add(notNegotiated(
                    "UPDATER_BUSINESS_PROTOCOL_NOT_NEGOTIATED",
                    "设备更新器尚未连接业务程序",
                    "设备更新器与业务程序尚未完成作业许可和健康检查通信协商，暂时无法确认设备可开展业务。"));
        } else {
            boolean mismatch = !fact.updaterBusiness().equals(
                    fact.updater().businessLocal())
                    || (release != null && !fact.updaterBusiness().equals(
                    release.updaterBusiness()));
            if (mismatch) {
                reasons.add(protocolMismatch(
                        "UPDATER_BUSINESS_PROTOCOL_MISMATCH",
                        "设备更新器与业务程序不兼容",
                        "设备更新器与业务程序协商出的作业许可和健康检查通信版本不在双方声明的兼容范围内。"));
            }
        }
    }

    private static void checkUart(
            Fact fact,
            Release release,
            List<Reason> reasons) {
        if ("INCOMPATIBLE".equals(fact.uartState())) {
            reasons.add(reason(
                    "UART_PROTOCOL_MISMATCH",
                    "业务程序与单片机通信不兼容",
                    "设备已经确认业务程序与单片机通信协议不兼容，已暂停新的物理业务。",
                    Severity.INCOMPATIBLE,
                    true));
            return;
        }
        if (!"READY".equals(fact.uartState())) {
            reasons.add(reason(
                    "UART_PROTOCOL_NOT_READY",
                    "单片机通信尚未就绪",
                    "设备尚未建立可用的单片机通信，平台暂时无法确认核心业务兼容性。",
                    Severity.UNKNOWN,
                    true));
            return;
        }
        if ("ECOBIN_UART".equals(release.uartFamily())) {
            if (fact.uartProtocol() == null) {
                reasons.add(reason(
                        "UART_PROTOCOL_NOT_REPORTED",
                        "未上报单片机通信版本",
                        "设备没有上报已协商的单片机通信主、次版本，平台暂时无法确认兼容性。",
                        Severity.UNKNOWN,
                        true));
            } else if (!fact.uartProtocol().equals(release.uartProtocol())) {
                reasons.add(protocolMismatch(
                        "UART_PROTOCOL_MISMATCH",
                        "业务程序与单片机通信不兼容",
                        "设备实际协商的单片机通信版本与当前业务发布声明不一致。"));
            }
            return;
        }
        if (fact.uartProtocol() != null) {
            reasons.add(protocolMismatch(
                    "UART_PROTOCOL_MISMATCH",
                    "业务程序与单片机通信不兼容",
                    "当前业务发布要求固定帧通信，但设备实际使用了带版本协商的单片机协议。"));
        } else if (fact.mcuFirmware() == null) {
            reasons.add(reason(
                    "MCU_FIRMWARE_IDENTITY_NOT_REPORTED",
                    "未上报单片机固件身份",
                    "设备没有上报单片机固件身份和固定帧修订，平台暂时无法确认兼容性。",
                    Severity.UNKNOWN,
                    true));
        } else if (!fact.mcuFirmware().fixedFrameRevision()
                .equals(release.fixedFrameRevision())) {
            reasons.add(protocolMismatch(
                    "UART_PROTOCOL_MISMATCH",
                    "业务程序与单片机通信不兼容",
                    "设备单片机的固定帧修订与当前业务发布要求不一致。"));
        }
    }

    private static void checkImageBridgeUart(
            Fact fact,
            List<Reason> reasons) {
        if ("INCOMPATIBLE".equals(fact.uartState())) {
            reasons.add(protocolMismatch(
                    "UART_PROTOCOL_MISMATCH",
                    "镜像内置业务程序与单片机通信不兼容",
                    "设备已经确认镜像内置业务程序与单片机通信协议不兼容，已暂停新的物理业务。"));
            return;
        }
        if (!"READY".equals(fact.uartState())) {
            reasons.add(reason(
                    "UART_PROTOCOL_NOT_READY",
                    "单片机通信尚未就绪",
                    "镜像内置业务程序尚未建立可用的单片机通信，平台暂时无法确认核心业务兼容性。",
                    Severity.UNKNOWN,
                    true));
            return;
        }
        if (fact.uartProtocol() == null && fact.mcuFirmware() == null) {
            reasons.add(reason(
                    "MCU_FIRMWARE_IDENTITY_NOT_REPORTED",
                    "未上报单片机通信身份",
                    "镜像内置业务程序没有上报可核对的单片机通信身份，平台暂时无法确认兼容性。",
                    Severity.UNKNOWN,
                    true));
        }
    }

    private static boolean imageBridge(Fact fact) {
        return fact.activeRelease() == null
                && fact.businessReady()
                && sameImageGeneration(
                        fact.agent().version(),
                        fact.updater().version());
    }

    private static boolean sameImageGeneration(
            String communicationVersion,
            String updaterVersion) {
        Matcher communication =
                IMAGE_COMMUNICATION_VERSION.matcher(communicationVersion);
        Matcher updater = IMAGE_UPDATER_VERSION.matcher(updaterVersion);
        return communication.matches()
                && updater.matches()
                && communication.group(1).equals(updater.group(1));
    }

    private static void checkMcuCapabilities(
            Fact fact,
            Release release,
            List<Reason> reasons) {
        long required = Long.parseUnsignedLong(
                release.requiredMcuCapabilities(), 16);
        long actual = Long.parseUnsignedLong(
                fact.capabilityBitmapHex(), 16);
        if ((actual & required) != required) {
            reasons.add(reason(
                    "MCU_CAPABILITY_MISMATCH",
                    "单片机缺少业务所需能力",
                    "设备单片机当前提供的能力不足以运行该业务程序，已暂停新的物理业务。",
                    Severity.INCOMPATIBLE,
                    true));
        }
    }

    private static void mismatch(
            Protocol actual,
            Protocol expected,
            String code,
            String title,
            String description,
            List<Reason> reasons) {
        if (!actual.equals(expected)) {
            reasons.add(protocolMismatch(code, title, description));
        }
    }

    private static Reason notNegotiated(
            String code,
            String title,
            String description) {
        return reason(code, title, description, Severity.UNKNOWN, true);
    }

    private static Reason protocolMismatch(
            String code,
            String title,
            String description) {
        return reason(
                code, title, description, Severity.INCOMPATIBLE, true);
    }

    private static Reason gateReason(String gate) {
        return switch (gate) {
            case "DRAINING" -> reason(
                    "DEVICE_DRAINING",
                    "设备正在等待现有作业结束",
                    "设备已经停止接收新作业，正在等待当前作业安全结束后进入维护。",
                    Severity.NONE,
                    true);
            case "MAINTENANCE" -> reason(
                    "DEVICE_IN_MAINTENANCE",
                    "设备正在维护",
                    "设备当前正在更新或执行维护操作，完成并重新上报就绪事实后才能接收新作业。",
                    Severity.NONE,
                    true);
            case "LOCKED" -> reason(
                    "DEVICE_LOCKED",
                    "设备因故障保持锁定",
                    "设备维护或恢复未能安全完成，需要先排除故障并由设备重新证明可运行。",
                    Severity.NONE,
                    true);
            default -> throw new IllegalArgumentException(
                    "unsupported business admission state");
        };
    }

    private static Severity strongest(List<Reason> reasons) {
        Severity result = Severity.NONE;
        for (Reason reason : reasons) {
            if (reason.severity() == Severity.INCOMPATIBLE) {
                return Severity.INCOMPATIBLE;
            }
            if (reason.severity() == Severity.UNKNOWN) {
                result = Severity.UNKNOWN;
            }
        }
        return result;
    }

    private String reasonsJson(List<Reason> reasons) {
        List<Map<String, Object>> values = new ArrayList<>();
        for (Reason reason : reasons) {
            Map<String, Object> value = new LinkedHashMap<>();
            value.put("code", reason.code());
            value.put("title", reason.title());
            value.put("description", reason.description());
            value.put("blocksNewBusiness", reason.blocksNewBusiness());
            values.add(value);
        }
        return objectMapper.writeValueAsString(values);
    }

    private String capabilitiesJson(Capabilities capabilities) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("coreBusiness", capabilities.coreBusiness());
        value.put("businessProgramRemoteUpdate",
                capabilities.businessProgramRemoteUpdate());
        value.put("mcuFirmwareRemoteUpdate",
                capabilities.mcuFirmwareRemoteUpdate());
        return objectMapper.writeValueAsString(value);
    }

    private static Fact parseFact(JsonNode event) {
        JsonNode payload = object(event, "payload");
        String eventUid = pattern(event, "eventUid", UUID_V4, 36);
        String payloadSha256 = pattern(event, "payloadSha256", SHA256, 64);
        requireText(payload, "managementArchitectureGeneration",
                "PERMANENT_V1");
        long sequence = integer(
                payload,
                "managementStateSequence",
                1,
                9_007_199_254_740_991L);
        String gate = enumText(payload, "businessAdmissionState", GATES);
        Agent agent = agent(object(payload, "communicationAgent"));
        Updater updater = updater(object(payload, "deviceUpdater"));
        ActiveRelease activeRelease = activeRelease(
                payload.get("activeBusinessRelease"));
        String processState = enumText(
                payload, "businessProcessState", PROCESS_STATES);
        boolean ready = bool(payload, "businessReady");
        JsonNode negotiated = object(payload, "negotiatedProtocols");
        Protocol agentBusiness = negotiated(
                negotiated,
                "agentBusinessNegotiated",
                "agentBusinessMajor",
                "agentBusinessMinor");
        Protocol agentUpdater = negotiated(
                negotiated,
                "agentUpdaterNegotiated",
                "agentUpdaterMajor",
                "agentUpdaterMinor");
        Protocol updaterBusiness = negotiated(
                negotiated,
                "updaterBusinessNegotiated",
                "updaterBusinessMajor",
                "updaterBusinessMinor");
        McuFirmware mcu = mcu(payload.get("mcuFirmware"));
        String uartState = enumText(payload, "uartState", UART_STATES);
        Protocol uartProtocol = nullableProtocol(payload.get("uartProtocol"));
        String capabilities = pattern(
                payload, "capabilityBitmapHex", BITMAP, 16);
        if (ready && (!"RUNNING".equals(processState)
                || agentBusiness == null
                || updaterBusiness == null
                || (activeRelease == null
                && !sameImageGeneration(
                        agent.version(), updater.version())))) {
            throw invalid(
                    "ready business process lacks a trusted release or image bridge identity");
        }
        LocalDateTime observedAt = observedAt(event);
        return new Fact(
                eventUid,
                sequence,
                gate,
                agent,
                updater,
                activeRelease,
                processState,
                ready,
                agentBusiness,
                agentUpdater,
                updaterBusiness,
                mcu,
                uartState,
                uartProtocol,
                capabilities,
                payloadSha256,
                observedAt);
    }

    private static Agent agent(JsonNode value) {
        return new Agent(
                version(value, "versionName"),
                protocol(
                        value,
                        "managementTransportProtocolMajor",
                        "managementTransportProtocolMinor"),
                protocol(
                        value,
                        "businessLocalProtocolMajor",
                        "businessLocalProtocolMinor"),
                protocol(
                        value,
                        "updaterLocalProtocolMajor",
                        "updaterLocalProtocolMinor"));
    }

    private static Updater updater(JsonNode value) {
        return new Updater(
                version(value, "versionName"),
                protocol(
                        value,
                        "deviceMaintenanceProtocolMajor",
                        "deviceMaintenanceProtocolMinor"),
                protocol(
                        value,
                        "businessLocalProtocolMajor",
                        "businessLocalProtocolMinor"),
                Math.toIntExact(integer(
                        value,
                        "businessPackageFormatVersion",
                        1,
                        65_535)),
                Math.toIntExact(integer(
                        value,
                        "mcuPackageFormatVersion",
                        1,
                        65_535)));
    }

    private static Protocol protocol(
            JsonNode parent,
            String majorField,
            String minorField) {
        return new Protocol(
                Math.toIntExact(integer(parent, majorField, 1, 255)),
                Math.toIntExact(integer(parent, minorField, 0, 255)));
    }

    private static Protocol negotiated(
            JsonNode parent,
            String presentField,
            String majorField,
            String minorField) {
        boolean present = bool(parent, presentField);
        int major = Math.toIntExact(integer(parent, majorField, 0, 255));
        int minor = Math.toIntExact(integer(parent, minorField, 0, 255));
        if (!present) {
            if (major != 0 || minor != 0) {
                throw invalid(
                        "an unnegotiated protocol must carry zero wire versions");
            }
            return null;
        }
        if (major == 0) {
            throw invalid(
                    "a negotiated protocol must carry a positive major version");
        }
        return new Protocol(major, minor);
    }

    private static ActiveRelease activeRelease(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isObject()) {
            throw invalid("activeBusinessRelease must be an object or null");
        }
        return new ActiveRelease(
                pattern(value, "releaseUid", UUID_V4, 36),
                integer(
                        value,
                        "releaseSequence",
                        1,
                        9_007_199_254_740_991L),
                version(value, "versionName"),
                pattern(value, "packageSha256", SHA256, 64));
    }

    private static McuFirmware mcu(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isObject()) {
            throw invalid("mcuFirmware must be an object or null");
        }
        return new McuFirmware(
                version(value, "versionName"),
                integer(value, "versionCode", 1, 4_294_967_295L),
                pattern(value, "identityHex", BITMAP, 16),
                Math.toIntExact(integer(
                        value, "fixedFrameRevision", 1, 255)));
    }

    private static Protocol nullableProtocol(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isObject()) {
            throw invalid("uartProtocol must be an object or null");
        }
        return new Protocol(
                Math.toIntExact(integer(value, "major", 1, 255)),
                Math.toIntExact(integer(value, "minor", 0, 255)));
    }

    private static Protocol nullableProtocol(Integer major, Integer minor) {
        if (major == null && minor == null) {
            return null;
        }
        if (major == null || minor == null) {
            throw new IllegalStateException(
                    "persisted protocol pair is incomplete");
        }
        return new Protocol(major, minor);
    }

    private static LocalDateTime observedAt(JsonNode event) {
        JsonNode value = event.get("occurredAt");
        if (value == null || value.isNull()) {
            return null;
        }
        try {
            return LocalDateTime.ofInstant(
                    Instant.parse(text(event, "occurredAt", 40)),
                    ZoneOffset.UTC);
        } catch (RuntimeException exception) {
            throw invalid("occurredAt must be an RFC3339 instant");
        }
    }

    private static JsonNode object(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isObject()) {
            throw invalid(field + " must be an object");
        }
        return value;
    }

    private static String text(
            JsonNode parent,
            String field,
            int maximumLength) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isTextual()
                || value.asText().isBlank()
                || value.asText().length() > maximumLength) {
            throw invalid(field + " must be bounded text");
        }
        return value.asText();
    }

    private static String version(JsonNode parent, String field) {
        return pattern(parent, field, VERSION, 32);
    }

    private static String pattern(
            JsonNode parent,
            String field,
            Pattern pattern,
            int maximumLength) {
        String value = text(parent, field, maximumLength);
        if (!pattern.matcher(value).matches()) {
            throw invalid(field + " has an invalid stable format");
        }
        return value;
    }

    private static long integer(
            JsonNode parent,
            String field,
            long minimum,
            long maximum) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isNumber()) {
            throw invalid(field + " is outside its supported range");
        }
        try {
            long exact = value.decimalValue().longValueExact();
            if (exact < minimum || exact > maximum) {
                throw invalid(field + " is outside its supported range");
            }
            return exact;
        } catch (ArithmeticException error) {
            throw invalid(field + " is outside its supported range");
        }
    }

    private static boolean bool(JsonNode parent, String field) {
        JsonNode value = parent == null ? null : parent.get(field);
        if (value == null || !value.isBoolean()) {
            throw invalid(field + " must be boolean");
        }
        return value.asBoolean();
    }

    private static String enumText(
            JsonNode parent,
            String field,
            Set<String> allowed) {
        String value = text(parent, field, 32);
        if (!allowed.contains(value)) {
            throw invalid(field + " is unsupported");
        }
        return value;
    }

    private static void requireText(
            JsonNode parent,
            String field,
            String expected) {
        if (!expected.equals(text(parent, field, 32))) {
            throw invalid(field + " differs from the supported generation");
        }
    }

    private static String uartFamily(Fact fact) {
        if (fact.uartProtocol() != null) {
            return "ECOBIN_UART";
        }
        if (fact.mcuFirmware() != null) {
            return "FIXED_FRAME";
        }
        return "UNKNOWN";
    }

    private static Integer nullableMajor(Protocol protocol) {
        return protocol == null ? null : protocol.major();
    }

    private static Integer nullableMinor(Protocol protocol) {
        return protocol == null ? null : protocol.minor();
    }

    private static <T> T nullableRelease(
            Fact fact,
            java.util.function.Function<ActiveRelease, T> getter) {
        return fact.activeRelease() == null
                ? null : getter.apply(fact.activeRelease());
    }

    private static <T> T nullableMcu(
            Fact fact,
            java.util.function.Function<McuFirmware, T> getter) {
        return fact.mcuFirmware() == null
                ? null : getter.apply(fact.mcuFirmware());
    }

    private static Reason reason(
            String code,
            String title,
            String description,
            Severity severity,
            boolean blocksNewBusiness) {
        return new Reason(
                code, title, description, severity, blocksNewBusiness);
    }

    private static IllegalArgumentException invalid(String message) {
        return new IllegalArgumentException(message);
    }

    private static UntrustedInboxSourceException untrusted(String message) {
        return new UntrustedInboxSourceException(message);
    }

    public record ApplyResult(boolean factInserted, boolean projectionChanged) {
        public boolean changed() {
            return factInserted || projectionChanged;
        }
    }

    private enum Severity {
        NONE,
        OPTIONAL,
        UNKNOWN,
        INCOMPATIBLE
    }

    private record Protocol(int major, int minor) {
    }

    private record Agent(
            String version,
            Protocol managementTransport,
            Protocol businessLocal,
            Protocol updaterLocal) {
    }

    private record Updater(
            String version,
            Protocol deviceMaintenance,
            Protocol businessLocal,
            int businessPackageFormatVersion,
            int mcuPackageFormatVersion) {
    }

    private record ActiveRelease(
            String uid,
            long sequence,
            String version,
            String packageSha256) {
    }

    private record McuFirmware(
            String version,
            long versionCode,
            String identityHex,
            Integer fixedFrameRevision) {
    }

    private record Fact(
            String eventUid,
            long managementStateSequence,
            String businessGateState,
            Agent agent,
            Updater updater,
            ActiveRelease activeRelease,
            String businessProcessState,
            boolean businessReady,
            Protocol agentBusiness,
            Protocol agentUpdater,
            Protocol updaterBusiness,
            McuFirmware mcuFirmware,
            String uartState,
            Protocol uartProtocol,
            String capabilityBitmapHex,
            String payloadSha256,
            LocalDateTime observedAt) {
    }

    private record ExistingFact(
            String eventUid,
            long sourceInboxId,
            long assetId,
            long managementStateSequence,
            String payloadSha256) {
    }

    private record ProjectionHead(
            String architectureGeneration,
            Long managementStateSequence,
            Long latestSoftwareFactId) {
    }

    private record Release(
            String uid,
            String version,
            long sequence,
            String packageSha256,
            int packageFormatVersion,
            int backendCommandContractVersion,
            int deviceEventContractVersion,
            Protocol communicationBusiness,
            Protocol updaterBusiness,
            String uartFamily,
            Protocol uartProtocol,
            Integer fixedFrameRevision,
            String requiredMcuCapabilities) {
    }

    private record Reason(
            String code,
            String title,
            String description,
            Severity severity,
            boolean blocksNewBusiness) {
    }

    private record Capabilities(
            Boolean coreBusiness,
            Boolean businessProgramRemoteUpdate,
            Boolean mcuFirmwareRemoteUpdate) {
    }

    private record Compatibility(
            String status,
            String admission,
            List<Reason> reasons,
            Capabilities capabilities) {
        private String primaryReasonCode() {
            return reasons.isEmpty() ? null : reasons.getFirst().code();
        }

        private String primaryReasonMessage() {
            return reasons.isEmpty()
                    ? null : reasons.getFirst().description();
        }
    }
}
