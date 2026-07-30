-- Materialize the stable public facts required by I-025 wallet timelines.
-- Wallet entries remain append-only. The public organization-user UID and
-- source number are copied when the ledger fact is created so funds can
-- answer its own queries without reading identity or recycling private
-- tables at runtime.

ALTER TABLE iam_organization_user
    ADD CONSTRAINT uq_iam_org_user_scope_id_uid UNIQUE (
        tenant_id,
        organization_id,
        id,
        organization_user_uid
    );

ALTER TABLE fund_user_wallet_entry
    ADD COLUMN organization_user_uid
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER organization_user_id,
    ADD COLUMN source_type
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER fund_phase,
    ADD COLUMN source_no
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER source_type;

UPDATE fund_user_wallet_entry entry_row
JOIN iam_organization_user user_row
  ON user_row.tenant_id = entry_row.tenant_id
 AND user_row.organization_id = entry_row.organization_id
 AND user_row.id = entry_row.organization_user_id
SET entry_row.organization_user_uid =
        user_row.organization_user_uid
WHERE entry_row.organization_user_uid IS NULL;

UPDATE fund_user_wallet_entry entry_row
JOIN rec_delivery_revision revision
  ON revision.tenant_id = entry_row.tenant_id
 AND revision.organization_id = entry_row.organization_id
 AND revision.id = entry_row.delivery_revision_id
JOIN rec_delivery_order order_row
  ON order_row.tenant_id = revision.tenant_id
 AND order_row.organization_id = revision.organization_id
 AND order_row.id = revision.delivery_order_id
SET entry_row.source_type = 'DELIVERY_ORDER',
    entry_row.source_no = order_row.delivery_order_no
WHERE entry_row.delivery_revision_id IS NOT NULL;

UPDATE fund_user_wallet_entry entry_row
JOIN fund_withdrawal_order withdrawal
  ON withdrawal.tenant_id = entry_row.tenant_id
 AND withdrawal.organization_id = entry_row.organization_id
 AND withdrawal.id = entry_row.withdrawal_order_id
SET entry_row.source_type = 'WITHDRAWAL_ORDER',
    entry_row.source_no = withdrawal.withdrawal_order_no
WHERE entry_row.withdrawal_order_id IS NOT NULL;

UPDATE fund_user_wallet_entry entry_row
JOIN fund_wallet_adjustment adjustment
  ON adjustment.tenant_id = entry_row.tenant_id
 AND adjustment.organization_id = entry_row.organization_id
 AND adjustment.id = entry_row.adjustment_id
SET entry_row.source_type = 'MANUAL_ADJUSTMENT',
    entry_row.source_no = adjustment.adjustment_uid
WHERE entry_row.adjustment_id IS NOT NULL;

ALTER TABLE fund_user_wallet_entry
    MODIFY COLUMN organization_user_uid
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    MODIFY COLUMN source_type
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    MODIFY COLUMN source_no
        VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_fund_wallet_entry_user_uid CHECK (
        organization_user_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT ck_fund_wallet_entry_public_source CHECK (
        BINARY source_no = BINARY TRIM(source_no)
        AND CHAR_LENGTH(source_no) BETWEEN 1 AND 64
        AND (
            (
                source_type = 'DELIVERY_ORDER'
                AND delivery_revision_id IS NOT NULL
                AND withdrawal_order_id IS NULL
                AND adjustment_id IS NULL
            )
            OR
            (
                source_type = 'WITHDRAWAL_ORDER'
                AND delivery_revision_id IS NULL
                AND withdrawal_order_id IS NOT NULL
                AND adjustment_id IS NULL
            )
            OR
            (
                source_type = 'MANUAL_ADJUSTMENT'
                AND delivery_revision_id IS NULL
                AND withdrawal_order_id IS NULL
                AND adjustment_id IS NOT NULL
            )
        )
    ),
    ADD CONSTRAINT fk_fund_wallet_entry_user_uid
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_user_id,
            organization_user_uid
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id,
            organization_user_uid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_fund_wallet_entry_org_public_page (
        tenant_id,
        organization_id,
        occurred_at,
        organization_user_uid,
        entry_sequence_no,
        visibility_sequence_no
    ),
    ADD INDEX ix_fund_wallet_entry_org_source (
        tenant_id,
        organization_id,
        source_no,
        occurred_at,
        organization_user_uid,
        entry_sequence_no
    );
