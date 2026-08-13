package org.enveloping.ecobin.device.application.installation;

import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceInstallationProfileView;
import org.enveloping.ecobin.device.web.v1.DeviceModels.UpdateDeviceInstallationProfileRequest;
import org.enveloping.ecobin.framework.audit.AuditActorKind;
import org.enveloping.ecobin.framework.audit.AuditEntry;
import org.enveloping.ecobin.framework.audit.AuditPort;
import org.enveloping.ecobin.framework.audit.AuditScopeKind;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.enveloping.ecobin.identity.api.port.StartCleanIdentityParticipationPort;
import org.enveloping.ecobin.identity.api.result.LockedCleanOrganizationUser;
import org.enveloping.ecobin.identity.api.result.LockedMiniappCleanScope;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * 清运员维护永久设备资产的当前安装资料。
 *
 * <p>安装资料不是机器运行配置。这里不会创建配置版本、下发 OneNet 命令，
 * 也不会改变设备的投递或清运资格。</p>
 */
@Service
public class DeviceInstallationProfileService {

    private static final String COORDINATE_SYSTEM = "GCJ02";
    private static final String ACTION = "device.installation-profile.update";
    private static final Pattern COORDINATE_TEXT = Pattern.compile(
            "^-?(?:0|[1-9][0-9]{0,2})(?:\\.[0-9]{1,7})?$");

    private final JdbcTemplate jdbc;
    private final StartCleanIdentityParticipationPort identity;
    private final AuditPort audit;

    public DeviceInstallationProfileService(
            JdbcTemplate jdbc,
            StartCleanIdentityParticipationPort identity,
            AuditPort audit) {
        this.jdbc = jdbc;
        this.identity = identity;
        this.audit = audit;
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceInstallationProfileView get(String requestedDeviceCode) {
        String deviceCode = normalizeDeviceCode(requestedDeviceCode);
        LockedMiniappCleanScope lockedScope =
                identity.lockCurrentMiniappScope();
        ScopeIds scope = lockedScope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(lockedScope);
        return cleaner.organizationUserRef().withOrganizationUserOnce(
                (tenantId, organizationId, ignoredUserId) -> {
                    requireScope(scope, tenantId, organizationId);
                    return view(find(
                            tenantId, organizationId, deviceCode, false));
                });
    }

    @Transactional(isolation = Isolation.READ_COMMITTED)
    public DeviceInstallationProfileView update(
            String requestedDeviceCode,
            UpdateDeviceInstallationProfileRequest request) {
        if (request == null || request.expectedVersion() == null) {
            throw invalid("设备安装资料请求不完整");
        }
        String deviceCode = normalizeDeviceCode(requestedDeviceCode);
        NormalizedProfile next = normalize(request);
        LockedMiniappCleanScope lockedScope =
                identity.lockCurrentMiniappScope();
        ScopeIds scope = lockedScope.organizationScopeRef()
                .withOrganizationScopeOnce(ScopeIds::new);
        LockedCleanOrganizationUser cleaner =
                identity.lockCurrentCleaner(lockedScope);
        UpdateResult result = cleaner.organizationUserRef()
                .withOrganizationUserOnce(
                        (tenantId, organizationId, organizationUserId) ->
                                {
                                    requireScope(
                                            scope, tenantId, organizationId);
                                    return updateLocked(
                                            tenantId,
                                            organizationId,
                                            organizationUserId,
                                            deviceCode,
                                            request.expectedVersion(),
                                            next);
                                });
        if (!result.changed()) {
            return view(result.profile());
        }

        cleaner.auditActorRef().withAuditActorOnce(
                (tenantId, organizationId, organizationUserId) -> {
                    if (tenantId != result.profile().tenantId()
                            || organizationId
                            != result.profile().organizationId()
                            || organizationUserId != result.updatedByUserId()) {
                        throw new IllegalStateException(
                                "installation profile audit actor changed identity");
                    }
                    audit.append(new AuditEntry(
                            UUID.randomUUID(),
                            UUID.randomUUID(),
                            UUID.randomUUID(),
                            AuditScopeKind.ORGANIZATION,
                            tenantId,
                            organizationId,
                            AuditActorKind.ORGANIZATION_USER,
                            null,
                            null,
                            organizationUserId,
                            null,
                            null,
                            ACTION,
                            "DEVICE_ASSET",
                            deviceCode,
                            "MINIAPP_USER",
                            "SUCCEEDED",
                            cleaner.loginSessionUid().value(),
                            null,
                            safeSummary(result.changedFields(),
                                    result.profile().version()),
                            Instant.now()));
                    return null;
                });
        return view(result.profile());
    }

    private UpdateResult updateLocked(
            long tenantId,
            long organizationId,
            long organizationUserId,
            String deviceCode,
            long expectedVersion,
            NormalizedProfile next) {
        ProfileRow current = find(
                tenantId, organizationId, deviceCode, true);
        boolean same = sameContent(current, next);
        if (expectedVersion == current.version() && same) {
            return new UpdateResult(current, false, organizationUserId,
                    List.of());
        }
        if (expectedVersion != current.version()) {
            // A network retry may carry the previous expected version after the
            // first response was lost. Returning the already stored content is
            // safe and does not create a second version.
            if (current.version() > 0
                    && expectedVersion == current.version() - 1
                    && same) {
                return new UpdateResult(current, false, organizationUserId,
                        List.of());
            }
            throw new TargetApiException(
                    409,
                    "COMMON.VERSION_CONFLICT",
                    "设备安装资料已被其他操作更新，请刷新后重试",
                    false,
                    Map.of("currentVersion", current.version()));
        }

        List<String> changedFields = changedFields(current, next);
        LocalDateTime now = databaseNow();
        int affected = jdbc.update("""
                UPDATE dev_device_asset
                SET installation_display_name = ?,
                    installation_address = ?,
                    installation_longitude = ?,
                    installation_latitude = ?,
                    installation_profile_version =
                        installation_profile_version + 1,
                    installation_updated_by_organization_user_id = ?,
                    installation_updated_at = ?,
                    updated_at = ?
                WHERE id = ?
                  AND tenant_id = ?
                  AND organization_id = ?
                  AND installation_profile_version = ?
                  AND lifecycle_status = 'NORMAL'
                  AND acceptance_status = 'PASSED'
                """,
                next.displayName(),
                next.address(),
                next.longitude(),
                next.latitude(),
                organizationUserId,
                now,
                now,
                current.id(),
                tenantId,
                organizationId,
                expectedVersion);
        if (affected != 1) {
            throw new TargetApiException(
                    409,
                    "COMMON.VERSION_CONFLICT",
                    "设备安装资料已被其他操作更新，请刷新后重试");
        }
        ProfileRow updated = find(
                tenantId, organizationId, deviceCode, false);
        return new UpdateResult(
                updated, true, organizationUserId, changedFields);
    }

    private ProfileRow find(
            long tenantId,
            long organizationId,
            String deviceCode,
            boolean lock) {
        return jdbc.query("""
                        SELECT id, tenant_id, organization_id,
                               device_public_code,
                               installation_display_name,
                               installation_address,
                               installation_longitude,
                               installation_latitude,
                               installation_profile_version,
                               installation_updated_at
                        FROM dev_device_asset
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND device_public_code = ?
                          AND lifecycle_status = 'NORMAL'
                          AND acceptance_status = 'PASSED'
                        """ + (lock ? " FOR UPDATE" : ""),
                (rs, ignored) -> row(rs),
                tenantId,
                organizationId,
                deviceCode).stream().findFirst()
                .orElseThrow(DeviceInstallationProfileService::notFound);
    }

    static NormalizedProfile normalize(
            UpdateDeviceInstallationProfileRequest request) {
        String displayName = required(
                request.displayName(), 100, "设备名称");
        String address = required(request.address(), 500, "安装地址");
        BigDecimal longitude = coordinate(
                request.longitude(), -180, 180, "经度");
        BigDecimal latitude = coordinate(
                request.latitude(), -90, 90, "纬度");
        return new NormalizedProfile(
                displayName, address, longitude, latitude);
    }

    private static BigDecimal coordinate(
            String value,
            int minimum,
            int maximum,
            String field) {
        if (value == null || value.isBlank()) {
            throw invalid(field + "不能为空");
        }
        String normalized = value.trim();
        if (!COORDINATE_TEXT.matcher(normalized).matches()) {
            throw invalid(field + "不是有效坐标");
        }
        try {
            BigDecimal result = new BigDecimal(normalized);
            if (result.compareTo(BigDecimal.valueOf(minimum)) < 0
                    || result.compareTo(BigDecimal.valueOf(maximum)) > 0) {
                throw invalid(field + "超出有效范围");
            }
            return result.setScale(7, RoundingMode.UNNECESSARY);
        } catch (NumberFormatException | ArithmeticException exception) {
            throw invalid(field + "不是有效坐标");
        }
    }

    private static String required(
            String value,
            int maximumLength,
            String field) {
        if (value == null || value.isBlank()) {
            throw invalid(field + "不能为空");
        }
        String normalized = value.trim();
        if (normalized.length() > maximumLength) {
            throw invalid(field + "长度超出限制");
        }
        return normalized;
    }

    private static String normalizeDeviceCode(String value) {
        if (value == null || !value.matches("^Dv_[A-Za-z0-9_-]{24,61}$")) {
            throw invalid("设备公开码格式无效");
        }
        return value;
    }

    private static boolean sameContent(
            ProfileRow current,
            NormalizedProfile next) {
        return Objects.equals(current.displayName(), next.displayName())
                && Objects.equals(current.address(), next.address())
                && decimalEquals(current.longitude(), next.longitude())
                && decimalEquals(current.latitude(), next.latitude());
    }

    private static List<String> changedFields(
            ProfileRow current,
            NormalizedProfile next) {
        List<String> fields = new ArrayList<>();
        if (!Objects.equals(current.displayName(), next.displayName())) {
            fields.add("displayName");
        }
        if (!Objects.equals(current.address(), next.address())) {
            fields.add("address");
        }
        if (!decimalEquals(current.longitude(), next.longitude())) {
            fields.add("longitude");
        }
        if (!decimalEquals(current.latitude(), next.latitude())) {
            fields.add("latitude");
        }
        return List.copyOf(fields);
    }

    private static boolean decimalEquals(
            BigDecimal left,
            BigDecimal right) {
        return left == null ? right == null
                : right != null && left.compareTo(right) == 0;
    }

    private static void requireScope(
            ScopeIds expected,
            long tenantId,
            long organizationId) {
        if (expected.tenantId() != tenantId
                || expected.organizationId() != organizationId) {
            throw new IllegalStateException(
                    "installation profile scope changed after authorization");
        }
    }

    private static DeviceInstallationProfileView view(ProfileRow row) {
        boolean complete = row.address() != null
                && !row.address().isBlank()
                && row.longitude() != null
                && row.latitude() != null;
        return new DeviceInstallationProfileView(
                row.deviceCode(),
                row.version(),
                complete,
                row.displayName(),
                row.address(),
                decimalString(row.longitude()),
                decimalString(row.latitude()),
                COORDINATE_SYSTEM,
                instant(row.updatedAt()));
    }

    private static ProfileRow row(ResultSet rs) throws SQLException {
        return new ProfileRow(
                rs.getLong("id"),
                rs.getLong("tenant_id"),
                rs.getLong("organization_id"),
                rs.getString("device_public_code"),
                rs.getString("installation_display_name"),
                rs.getString("installation_address"),
                rs.getBigDecimal("installation_longitude"),
                rs.getBigDecimal("installation_latitude"),
                rs.getLong("installation_profile_version"),
                rs.getObject("installation_updated_at", LocalDateTime.class));
    }

    private LocalDateTime databaseNow() {
        LocalDateTime value = jdbc.queryForObject(
                "SELECT CURRENT_TIMESTAMP(3)", LocalDateTime.class);
        if (value == null) {
            throw new IllegalStateException("database clock unavailable");
        }
        return value;
    }

    private static String decimalString(BigDecimal value) {
        if (value == null) {
            return null;
        }
        BigDecimal normalized = value.stripTrailingZeros();
        if (normalized.scale() < 0) {
            normalized = normalized.setScale(0);
        }
        return normalized.toPlainString();
    }

    private static Instant instant(LocalDateTime value) {
        return value == null ? null : value.toInstant(ZoneOffset.UTC);
    }

    private static String safeSummary(
            List<String> changedFields,
            long version) {
        String fields = changedFields.stream()
                .map(field -> "\"" + field + "\"")
                .reduce((left, right) -> left + "," + right)
                .orElse("");
        return "{\"changedFields\":[" + fields
                + "],\"version\":" + version + "}";
    }

    private static TargetApiException invalid(String message) {
        return new TargetApiException(400, "COMMON.INVALID_ARGUMENT", message);
    }

    private static TargetApiException notFound() {
        return new TargetApiException(
                404,
                "COMMON.NOT_FOUND",
                "设备不存在或当前机构无权查看");
    }

    record NormalizedProfile(
            String displayName,
            String address,
            BigDecimal longitude,
            BigDecimal latitude) {
    }

    private record ScopeIds(long tenantId, long organizationId) {
    }

    private record ProfileRow(
            long id,
            long tenantId,
            long organizationId,
            String deviceCode,
            String displayName,
            String address,
            BigDecimal longitude,
            BigDecimal latitude,
            long version,
            LocalDateTime updatedAt) {
    }

    private record UpdateResult(
            ProfileRow profile,
            boolean changed,
            long updatedByUserId,
            List<String> changedFields) {
    }
}
