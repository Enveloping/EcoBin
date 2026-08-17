-- V54 adds an explicit settlement-amount ceiling for delivery auto review.
-- Existing orders are not backfilled. An existing automatic rule has no
-- trustworthy amount ceiling, so refuse the migration before changing DDL.

-- Use a connection-local prepared statement instead of a stored procedure.
-- If the guard fails, no schema object remains behind.
SET @p0_v54_guard_sql = (
    SELECT IF(
        EXISTS (
            SELECT 1
            FROM rec_organization_delivery_config
            WHERE review_mode <> 'ALL_MANUAL'
            LIMIT 1
        ) OR EXISTS (
            SELECT 1
            FROM rec_delivery_order
            WHERE review_mode_snapshot <> 'ALL_MANUAL'
            LIMIT 1
        ),
        'SELECT 1 FROM p0_v54_blocked_legacy_auto_review_requires_amount_limit',
        'SELECT 1'
    )
);
PREPARE p0_v54_guard FROM @p0_v54_guard_sql;
EXECUTE p0_v54_guard;
DEALLOCATE PREPARE p0_v54_guard;
SET @p0_v54_guard_sql = NULL;

ALTER TABLE rec_organization_delivery_config
    DROP CHECK ck_rec_delivery_config_v53,
    ADD COLUMN automatic_review_max_amount_cent BIGINT NULL
        AFTER review_mode,
    ADD CONSTRAINT ck_rec_delivery_config_v54 CHECK (
        review_mode IN (
            'ALL_MANUAL',
            'NORMAL_AUTO_IMMEDIATE',
            'NORMAL_AUTO_AFTER_24H',
            'NORMAL_AUTO_AFTER_48H'
        )
        AND (
            (
                review_mode = 'ALL_MANUAL'
                AND automatic_review_max_amount_cent IS NULL
            )
            OR
            (
                review_mode <> 'ALL_MANUAL'
                AND automatic_review_max_amount_cent IS NOT NULL
                AND automatic_review_max_amount_cent >= 0
            )
        )
        AND open_balance_floor_cent < 0
        AND max_review_abs_weight_g BETWEEN 1 AND 1000000
    );

ALTER TABLE rec_delivery_order
    DROP CHECK ck_rec_delivery_order_auto_review,
    ADD COLUMN automatic_review_max_amount_cent_snapshot BIGINT NULL
        AFTER review_mode_snapshot,
    ADD CONSTRAINT ck_rec_delivery_order_auto_review_v54 CHECK (
        (
            review_mode_snapshot = 'ALL_MANUAL'
            AND automatic_review_max_amount_cent_snapshot IS NULL
            AND automatic_review_due_at IS NULL
        )
        OR
        (
            review_mode_snapshot IN (
                'NORMAL_AUTO_IMMEDIATE',
                'NORMAL_AUTO_AFTER_24H',
                'NORMAL_AUTO_AFTER_48H'
            )
            AND automatic_review_max_amount_cent_snapshot IS NOT NULL
            AND automatic_review_max_amount_cent_snapshot >= 0
            AND automatic_review_due_at IS NOT NULL
            AND automatic_review_due_at >= backend_received_at
        )
    );
