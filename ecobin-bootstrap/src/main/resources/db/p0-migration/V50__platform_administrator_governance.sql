-- V50: distinguish the protected default platform administrator from
-- ordinary platform administrators and support irreversible logical deletion.

ALTER TABLE iam_platform_admin
    ADD COLUMN admin_kind VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'STANDARD'
        AFTER platform_admin_uid,
    ADD COLUMN deleted_at DATETIME(3) NULL
        AFTER password_changed_at;

-- The only pre-V50 production administrator is the requested default account.
-- Refuse to elevate an arbitrary account when legacy data is ambiguous.
SET @v50_platform_admin_count = (
    SELECT COUNT(*)
    FROM iam_platform_admin
);

UPDATE iam_platform_admin
SET admin_kind = 'DEFAULT'
WHERE @v50_platform_admin_count = 1
  AND login_name = 'enveloping';

ALTER TABLE iam_platform_admin
    ADD COLUMN default_admin_slot TINYINT
        GENERATED ALWAYS AS (
            CASE WHEN admin_kind = 'DEFAULT' THEN 1 ELSE NULL END
        ) STORED
        AFTER deleted_at,
    ADD CONSTRAINT uq_iam_platform_admin_default_slot
        UNIQUE (default_admin_slot),
    ADD CONSTRAINT ck_iam_platform_admin_kind_v50 CHECK (
        admin_kind IN ('DEFAULT', 'STANDARD')
    ),
    ADD CONSTRAINT ck_iam_platform_admin_default_v50 CHECK (
        admin_kind <> 'DEFAULT'
        OR (enabled = 1 AND deleted_at IS NULL)
    ),
    ADD CONSTRAINT ck_iam_platform_admin_deleted_v50 CHECK (
        deleted_at IS NULL OR enabled = 0
    ),
    ADD CONSTRAINT ck_iam_platform_admin_deleted_time_v50 CHECK (
        deleted_at IS NULL
        OR (deleted_at >= created_at AND updated_at >= deleted_at)
    ),
    ADD INDEX ix_iam_platform_admin_lifecycle_v50 (
        deleted_at,
        enabled,
        admin_kind,
        login_name
    );
