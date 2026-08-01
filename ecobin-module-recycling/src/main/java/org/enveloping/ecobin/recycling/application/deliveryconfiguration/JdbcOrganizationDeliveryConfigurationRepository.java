package org.enveloping.ecobin.recycling.application.deliveryconfiguration;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Repository;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
class JdbcOrganizationDeliveryConfigurationRepository
        implements OrganizationDeliveryConfigurationRepository {

    private static final String VERSION_SELECT = """
            SELECT config.id,
                   config.version_no,
                   config.content_sha256,
                   config.review_mode,
                   config.open_balance_floor_cent,
                   config.max_review_abs_weight_g,
                   config.publication_source,
                   staff.staff_account_uid,
                   CASE
                       WHEN staff.id IS NOT NULL THEN staff.display_name
                       WHEN config.publication_source = 'SYSTEM'
                           THEN '系统或平台'
                       ELSE '未知发布人'
                   END AS published_by,
                   config.published_at,
                   CASE
                       WHEN head.current_config_id = config.id THEN 1
                       ELSE 0
                   END AS is_current,
                   head.lock_version AS head_lock_version
            FROM rec_organization_delivery_config config
            JOIN rec_organization_delivery_config_head head
              ON head.tenant_id = config.tenant_id
             AND head.organization_id = config.organization_id
            LEFT JOIN iam_staff_account staff
              ON staff.tenant_id = config.tenant_id
             AND staff.id = config.published_by_staff_account_id
            """;

    private static final RowMapper<DeliveryConfigurationRow> ROW_MAPPER =
            JdbcOrganizationDeliveryConfigurationRepository::mapRow;

    private final JdbcTemplate jdbc;

    JdbcOrganizationDeliveryConfigurationRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<DeliveryConfigurationRow> findCurrent(
            DeliveryConfigurationScope scope) {
        return jdbc.query(
                        VERSION_SELECT + """
                                WHERE config.tenant_id = ?
                                  AND config.organization_id = ?
                                  AND config.id = head.current_config_id
                                  AND config.version_no =
                                      head.current_version_no
                                """,
                        ROW_MAPPER,
                        scope.tenantId(),
                        scope.organizationId())
                .stream()
                .findFirst();
    }

    @Override
    public Optional<DeliveryConfigurationRow> lockCurrent(
            DeliveryConfigurationScope scope) {
        List<HeadRow> heads = jdbc.query("""
                        SELECT current_config_id,
                               current_version_no,
                               lock_version
                        FROM rec_organization_delivery_config_head
                        WHERE tenant_id = ?
                          AND organization_id = ?
                        FOR UPDATE
                        """,
                (rs, ignored) -> new HeadRow(
                        rs.getLong("current_config_id"),
                        rs.getLong("current_version_no"),
                        rs.getLong("lock_version")),
                scope.tenantId(),
                scope.organizationId());
        if (heads.size() != 1) {
            return Optional.empty();
        }
        HeadRow head = heads.getFirst();
        return jdbc.query(
                        VERSION_SELECT + """
                                WHERE config.tenant_id = ?
                                  AND config.organization_id = ?
                                  AND config.id = ?
                                  AND config.version_no = ?
                                """,
                        ROW_MAPPER,
                        scope.tenantId(),
                        scope.organizationId(),
                        head.configurationId(),
                        head.versionNo())
                .stream()
                .findFirst()
                .map(row -> new DeliveryConfigurationRow(
                        row.id(),
                        row.versionNo(),
                        row.contentSha256(),
                        row.reviewMode(),
                        row.openBalanceFloorCent(),
                        row.maxReviewAbsoluteWeightGram(),
                        row.publicationSource(),
                        row.publishedByStaffAccountUid(),
                        row.publishedBy(),
                        row.publishedAt(),
                        true,
                        head.lockVersion()));
    }

    @Override
    public Optional<DeliveryConfigurationRow> findVersion(
            DeliveryConfigurationScope scope,
            long versionNo) {
        return jdbc.query(
                        VERSION_SELECT + """
                                WHERE config.tenant_id = ?
                                  AND config.organization_id = ?
                                  AND config.version_no = ?
                                """,
                        ROW_MAPPER,
                        scope.tenantId(),
                        scope.organizationId(),
                        versionNo)
                .stream()
                .findFirst();
    }

    @Override
    public List<DeliveryConfigurationRow> findVersions(
            DeliveryConfigurationScope scope,
            Long beforeVersionNo,
            int limit) {
        if (beforeVersionNo == null) {
            return jdbc.query(
                    VERSION_SELECT + """
                            WHERE config.tenant_id = ?
                              AND config.organization_id = ?
                            ORDER BY config.version_no DESC
                            LIMIT ?
                            """,
                    ROW_MAPPER,
                    scope.tenantId(),
                    scope.organizationId(),
                    limit);
        }
        return jdbc.query(
                VERSION_SELECT + """
                        WHERE config.tenant_id = ?
                          AND config.organization_id = ?
                          AND config.version_no < ?
                        ORDER BY config.version_no DESC
                        LIMIT ?
                        """,
                ROW_MAPPER,
                scope.tenantId(),
                scope.organizationId(),
                beforeVersionNo,
                limit);
    }

    @Override
    public long insertVersion(
            DeliveryConfigurationScope scope,
            NewDeliveryConfigurationVersion version) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO rec_organization_delivery_config (
                        tenant_id,
                        organization_id,
                        version_no,
                        content_sha256,
                        review_mode,
                        open_balance_floor_cent,
                        max_review_abs_weight_g,
                        publication_source,
                        published_by_staff_account_id,
                        published_at,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setLong(1, scope.tenantId());
            statement.setLong(2, scope.organizationId());
            statement.setLong(3, version.versionNo());
            statement.setBytes(4, version.contentSha256());
            statement.setString(5, version.reviewMode());
            statement.setLong(6, version.openBalanceFloorCent());
            statement.setLong(
                    7,
                    version.maxReviewAbsoluteWeightGram());
            statement.setString(8, version.publicationSource());
            if (version.publishedByStaffAccountId() == null) {
                statement.setNull(9, java.sql.Types.BIGINT);
            } else {
                statement.setLong(
                        9,
                        version.publishedByStaffAccountId());
            }
            statement.setObject(10, version.publishedAt());
            statement.setObject(11, version.publishedAt());
            return statement;
        }, keyHolder);
        Number key = keyHolder.getKey();
        if (key == null) {
            throw new IllegalStateException(
                    "delivery configuration key was not returned");
        }
        return key.longValue();
    }

    @Override
    public void switchCurrent(
            DeliveryConfigurationScope scope,
            DeliveryConfigurationRow previous,
            long configurationId,
            long versionNo,
            LocalDateTime switchedAt) {
        int updated = jdbc.update("""
                        UPDATE rec_organization_delivery_config_head
                        SET current_config_id = ?,
                            current_version_no = ?,
                            lock_version = lock_version + 1,
                            switched_at = ?,
                            updated_at = ?
                        WHERE tenant_id = ?
                          AND organization_id = ?
                          AND current_config_id = ?
                          AND current_version_no = ?
                          AND lock_version = ?
                        """,
                configurationId,
                versionNo,
                switchedAt,
                switchedAt,
                scope.tenantId(),
                scope.organizationId(),
                previous.id(),
                previous.versionNo(),
                previous.headLockVersion());
        if (updated != 1) {
            throw new IllegalStateException(
                    "delivery configuration head changed while locked");
        }
    }

    private static DeliveryConfigurationRow mapRow(
            ResultSet rs,
            int ignored) throws SQLException {
        String publisherUid = rs.getString("staff_account_uid");
        return new DeliveryConfigurationRow(
                rs.getLong("id"),
                rs.getLong("version_no"),
                rs.getBytes("content_sha256"),
                rs.getString("review_mode"),
                rs.getLong("open_balance_floor_cent"),
                rs.getLong("max_review_abs_weight_g"),
                rs.getString("publication_source"),
                publisherUid == null
                        ? null
                        : UUID.fromString(publisherUid),
                rs.getString("published_by"),
                rs.getObject("published_at", LocalDateTime.class),
                rs.getBoolean("is_current"),
                rs.getLong("head_lock_version"));
    }

    private record HeadRow(
            long configurationId,
            long versionNo,
            long lockVersion) {
    }
}
