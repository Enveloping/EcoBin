-- 新机构由 funds 事务参与者同步发布默认提现配置。
-- 本迁移仅为既有机构补齐相同的 v1 默认值和 head，不改写已有配置。

INSERT INTO fund_organization_withdraw_config (
    tenant_id,
    organization_id,
    version_no,
    content_sha256,
    hard_limit_cent,
    manual_min_cent,
    manual_max_cent,
    manual_review_free_threshold_cent,
    publication_source,
    published_by_staff_account_id,
    published_at,
    created_at
)
SELECT
    organization.tenant_id,
    organization.id,
    1,
    UNHEX(SHA2('hard=1000;min=10;max=1000;reviewFree=0', 256)),
    1000,
    10,
    1000,
    0,
    'SYSTEM',
    NULL,
    UTC_TIMESTAMP(3),
    UTC_TIMESTAMP(3)
FROM iam_organization organization
LEFT JOIN fund_organization_withdraw_config config
  ON config.tenant_id = organization.tenant_id
 AND config.organization_id = organization.id
WHERE config.id IS NULL;

INSERT INTO fund_organization_withdraw_config_head (
    organization_id,
    tenant_id,
    current_config_id,
    current_version_no,
    lock_version,
    switched_at,
    updated_at
)
SELECT
    organization.id,
    organization.tenant_id,
    config.id,
    config.version_no,
    0,
    UTC_TIMESTAMP(3),
    UTC_TIMESTAMP(3)
FROM iam_organization organization
JOIN fund_organization_withdraw_config config
  ON config.tenant_id = organization.tenant_id
 AND config.organization_id = organization.id
 AND config.version_no = 1
LEFT JOIN fund_organization_withdraw_config_head head
  ON head.tenant_id = organization.tenant_id
 AND head.organization_id = organization.id
WHERE head.organization_id IS NULL;

CREATE TEMPORARY TABLE v29_withdrawal_default_guard (
    mismatch_count BIGINT NOT NULL,
    CONSTRAINT ck_v29_withdrawal_default_guard CHECK (mismatch_count = 0)
);

INSERT INTO v29_withdrawal_default_guard (mismatch_count)
SELECT COUNT(*)
FROM iam_organization organization
LEFT JOIN fund_organization_withdraw_config_head head
  ON head.tenant_id = organization.tenant_id
 AND head.organization_id = organization.id
WHERE head.organization_id IS NULL;

DROP TEMPORARY TABLE v29_withdrawal_default_guard;
