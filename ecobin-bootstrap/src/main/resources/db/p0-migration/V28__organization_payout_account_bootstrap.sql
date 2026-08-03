-- 新机构由 funds 的 OrganizationBootstrapParticipant 同事务创建机构出款账户。
-- 本迁移只补齐该参与者上线前已经存在且尚无账户的机构，不覆盖任何已有资金事实。

INSERT INTO fund_organization_payout_account (
    account_uid,
    tenant_id,
    organization_id,
    available_payout_cent,
    frozen_withdrawal_cent,
    lock_version,
    created_at,
    updated_at
)
SELECT
    LOWER(CONCAT(
        SUBSTRING(source.digest, 1, 8), '-',
        SUBSTRING(source.digest, 9, 4), '-4',
        SUBSTRING(source.digest, 14, 3), '-8',
        SUBSTRING(source.digest, 18, 3), '-',
        SUBSTRING(source.digest, 21, 12)
    )),
    source.tenant_id,
    source.organization_id,
    0,
    0,
    0,
    UTC_TIMESTAMP(3),
    UTC_TIMESTAMP(3)
FROM (
    SELECT
        organization.tenant_id,
        organization.id AS organization_id,
        MD5(CONCAT(
            'ecobin:organization-payout-account:',
            organization.tenant_id,
            ':',
            organization.id
        )) AS digest
    FROM iam_organization organization
    LEFT JOIN fund_organization_payout_account account
        ON account.tenant_id = organization.tenant_id
       AND account.organization_id = organization.id
    WHERE account.id IS NULL
) source;

CREATE TEMPORARY TABLE v28_payout_account_guard (
    mismatch_count BIGINT NOT NULL,
    CONSTRAINT ck_v28_payout_account_guard CHECK (mismatch_count = 0)
);

INSERT INTO v28_payout_account_guard (mismatch_count)
SELECT COUNT(*)
FROM iam_organization organization
LEFT JOIN fund_organization_payout_account account
    ON account.tenant_id = organization.tenant_id
   AND account.organization_id = organization.id
WHERE account.id IS NULL;

DROP TEMPORARY TABLE v28_payout_account_guard;
