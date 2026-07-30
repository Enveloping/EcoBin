-- Give every existing organization the same runnable recycling defaults that
-- new organizations receive from OrganizationBootstrapParticipant.

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
)
SELECT
    organization.tenant_id,
    organization.id,
    COALESCE(latest.latest_version_no, 0) + 1,
    UNHEX(SHA2(
        '{"maxReviewAbsoluteWeightGram":100000,"openBalanceFloorCent":-1000,"reviewMode":"ALL_MANUAL","schemaVersion":1}',
        256
    )),
    'ALL_MANUAL',
    -1000,
    100000,
    'SYSTEM',
    NULL,
    UTC_TIMESTAMP(3),
    UTC_TIMESTAMP(3)
FROM iam_organization organization
LEFT JOIN (
    SELECT
        tenant_id,
        organization_id,
        MAX(version_no) AS latest_version_no
    FROM rec_organization_delivery_config
    GROUP BY tenant_id, organization_id
) latest
  ON latest.tenant_id = organization.tenant_id
 AND latest.organization_id = organization.id
LEFT JOIN rec_organization_delivery_config_head head
  ON head.tenant_id = organization.tenant_id
 AND head.organization_id = organization.id
WHERE head.organization_id IS NULL;

INSERT INTO rec_organization_delivery_config_head (
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
    current_config.id,
    current_config.version_no,
    0,
    UTC_TIMESTAMP(3),
    UTC_TIMESTAMP(3)
FROM iam_organization organization
JOIN rec_organization_delivery_config current_config
  ON current_config.tenant_id = organization.tenant_id
 AND current_config.organization_id = organization.id
LEFT JOIN rec_organization_delivery_config newer_config
  ON newer_config.tenant_id = current_config.tenant_id
 AND newer_config.organization_id = current_config.organization_id
 AND newer_config.version_no > current_config.version_no
LEFT JOIN rec_organization_delivery_config_head head
  ON head.tenant_id = organization.tenant_id
 AND head.organization_id = organization.id
WHERE newer_config.id IS NULL
  AND head.organization_id IS NULL;

INSERT INTO rec_organization_order_counter (
    organization_id,
    tenant_id,
    last_visibility_sequence_no,
    lock_version,
    updated_at
)
SELECT
    organization.id,
    organization.tenant_id,
    0,
    0,
    UTC_TIMESTAMP(3)
FROM iam_organization organization
LEFT JOIN rec_organization_order_counter counter
  ON counter.tenant_id = organization.tenant_id
 AND counter.organization_id = organization.id
WHERE counter.organization_id IS NULL;
