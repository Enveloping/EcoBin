package org.enveloping.ecobin.recycling.infrastructure.registration;

import org.enveloping.ecobin.identity.api.command.OrganizationBootstrapCommand;
import org.enveloping.ecobin.identity.api.port.OrganizationBootstrapParticipant;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.jdbc.support.KeyHolder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.sql.Statement;

/**
 * 在机构创建事务内写入回收模块运行所需的默认投递、清运规则和序号头。
 */
@Component
public class JdbcOrganizationBootstrapParticipant
        implements OrganizationBootstrapParticipant {

    private static final String REVIEW_MODE = "ALL_MANUAL";
    private static final int DELIVERY_SCHEMA_VERSION = 1;
    private static final int CLEAN_SCHEMA_VERSION = 2;

    private final JdbcTemplate jdbc;
    private final DefaultOrganizationDeliveryRuleProperties deliveryProperties;
    private final DefaultOrganizationCleanRuleProperties cleanProperties;

    public JdbcOrganizationBootstrapParticipant(
            JdbcTemplate jdbc,
            DefaultOrganizationDeliveryRuleProperties deliveryProperties,
            DefaultOrganizationCleanRuleProperties cleanProperties) {
        this.jdbc = jdbc;
        this.deliveryProperties = deliveryProperties;
        this.cleanProperties = cleanProperties;
    }

    @Override
    @Transactional(propagation = Propagation.REQUIRED)
    public void initializeOrganization(
            OrganizationBootstrapCommand command) {
        deliveryProperties.validate();
        cleanProperties.validate();
        command.persistenceRef().writeForeignKeyTo(
                (tenantKey, organizationKey) -> {
                    KeyHolder keyHolder = new GeneratedKeyHolder();
                    jdbc.update(connection -> {
                        var statement = connection.prepareStatement("""
                                INSERT INTO rec_organization_delivery_config (
                                    tenant_id, organization_id, version_no,
                                    content_sha256, review_mode,
                                    automatic_review_max_amount_cent,
                                    open_balance_floor_cent,
                                    max_review_abs_weight_g,
                                    publication_source,
                                    published_by_staff_account_id,
                                    published_at, created_at
                                ) VALUES (
                                    ?, ?, 1, ?, 'ALL_MANUAL',
                                    NULL, ?, ?, 'SYSTEM', NULL,
                                    UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                                )
                                """, Statement.RETURN_GENERATED_KEYS);
                        statement.setLong(1, tenantKey);
                        statement.setLong(2, organizationKey);
                        statement.setBytes(3, deliveryContentSha256());
                        statement.setLong(
                                4,
                                deliveryProperties
                                        .getOpenBalanceFloorCent());
                        statement.setLong(
                                5,
                                deliveryProperties
                                        .getMaximumReviewAbsoluteWeightGram());
                        return statement;
                    }, keyHolder);
                    Number generatedKey = keyHolder.getKey();
                    if (generatedKey == null) {
                        throw new IllegalStateException(
                                "default delivery rule key was not returned");
                    }
                    jdbc.update("""
                                    INSERT INTO
                                        rec_organization_delivery_config_head (
                                            organization_id, tenant_id,
                                            current_config_id,
                                            current_version_no,
                                            lock_version,
                                            switched_at, updated_at
                                        ) VALUES (
                                            ?, ?, ?, 1, 0,
                                            UTC_TIMESTAMP(3),
                                            UTC_TIMESTAMP(3)
                                        )
                                    """,
                            organizationKey,
                            tenantKey,
                            generatedKey.longValue());
                    jdbc.update("""
                                    INSERT INTO rec_organization_order_counter (
                                        organization_id, tenant_id,
                                        last_visibility_sequence_no,
                                        lock_version, updated_at
                                    ) VALUES (
                                        ?, ?, 0, 0, UTC_TIMESTAMP(3)
                                    )
                                    """,
                            organizationKey,
                            tenantKey);
                    initializeCleanRule(
                            tenantKey,
                            organizationKey);
                });
    }

    private void initializeCleanRule(
            long tenantKey,
            long organizationKey) {
        KeyHolder keyHolder = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            var statement = connection.prepareStatement("""
                    INSERT INTO rec_organization_clean_config (
                        tenant_id, organization_id, version_no,
                        content_sha256, operation_timeout_seconds,
                        publication_source,
                        published_by_staff_account_id,
                        published_at, created_at
                    ) VALUES (
                        ?, ?, 1, ?, ?, 'SYSTEM', NULL,
                        UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                    )
                    """, Statement.RETURN_GENERATED_KEYS);
            statement.setLong(1, tenantKey);
            statement.setLong(2, organizationKey);
            statement.setBytes(3, cleanContentSha256());
            statement.setInt(
                    4,
                    cleanProperties.getOperationTimeoutSeconds());
            return statement;
        }, keyHolder);
        Number generatedKey = keyHolder.getKey();
        if (generatedKey == null) {
            throw new IllegalStateException(
                    "default clean rule key was not returned");
        }
        jdbc.update("""
                        INSERT INTO rec_organization_clean_config_head (
                            organization_id, tenant_id,
                            current_config_id, current_version_no,
                            lock_version, switched_at, updated_at
                        ) VALUES (
                            ?, ?, ?, 1, 0,
                            UTC_TIMESTAMP(3), UTC_TIMESTAMP(3)
                        )
                        """,
                organizationKey,
                tenantKey,
                generatedKey.longValue());
        jdbc.update("""
                        INSERT INTO rec_organization_clean_record_counter (
                            organization_id, tenant_id,
                            last_visibility_sequence_no,
                            lock_version, updated_at
                        ) VALUES (
                            ?, ?, 0, 0, UTC_TIMESTAMP(3)
                        )
                        """,
                organizationKey,
                tenantKey);
    }

    private byte[] deliveryContentSha256() {
        String canonical = """
                {"maxReviewAbsoluteWeightGram":%d,"openBalanceFloorCent":%d,"reviewMode":"%s","schemaVersion":%d}"""
                .formatted(
                        deliveryProperties
                                .getMaximumReviewAbsoluteWeightGram(),
                        deliveryProperties.getOpenBalanceFloorCent(),
                        REVIEW_MODE,
                        DELIVERY_SCHEMA_VERSION);
        return sha256(canonical);
    }

    private byte[] cleanContentSha256() {
        String canonical = """
                {"operationTimeoutSeconds":%d,"schemaVersion":%d}"""
                .formatted(
                        cleanProperties.getOperationTimeoutSeconds(),
                        CLEAN_SCHEMA_VERSION);
        return sha256(canonical);
    }

    private static byte[] sha256(String canonical) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(
                    canonical.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
