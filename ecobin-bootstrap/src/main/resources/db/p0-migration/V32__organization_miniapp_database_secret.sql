-- Institution mini-program credentials now have one migration boundary with
-- the rest of the identity configuration. Historical secret_ref values point
-- to disposable test files, so they are deliberately not copied as secrets.

ALTER TABLE iam_organization_miniapp
    ADD COLUMN app_secret VARCHAR(256)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        COMMENT '机构小程序 AppSecret 明文；日志和审计必须脱敏'
        AFTER login_enabled;

-- Existing references are known test credentials. Disable their login entry
-- and revoke every session that was authenticated through them. The current
-- target environment has no business users, but keeping the invalidation in
-- the forward migration prevents an old token from becoming valid again if a
-- copied or later environment re-enables the mini-program within its TTL.
UPDATE iam_organization_user_session AS session_row
JOIN iam_organization_miniapp AS miniapp
  ON miniapp.tenant_id = session_row.tenant_id
 AND miniapp.organization_id = session_row.organization_id
 AND miniapp.id = session_row.organization_miniapp_id
SET session_row.revoked_at = UTC_TIMESTAMP(3),
    session_row.revocation_reason =
        'MINIAPP_CREDENTIAL_STORAGE_MIGRATED'
WHERE session_row.revoked_at IS NULL;

UPDATE iam_staff_login_session AS session_row
JOIN iam_organization_miniapp AS miniapp
  ON miniapp.tenant_id = session_row.tenant_id
 AND miniapp.id = session_row.organization_miniapp_id
SET session_row.revoked_at = UTC_TIMESTAMP(3),
    session_row.revocation_reason =
        'MINIAPP_CREDENTIAL_STORAGE_MIGRATED'
WHERE session_row.client_kind = 'MINIAPP_MANAGEMENT'
  AND session_row.revoked_at IS NULL;

UPDATE iam_organization_miniapp
SET login_enabled = 0,
    lock_version = lock_version + 1,
    updated_at = UTC_TIMESTAMP(3);

ALTER TABLE iam_organization_miniapp
    DROP CHECK ck_iam_miniapp_secret_ref;

ALTER TABLE iam_organization_miniapp
    DROP COLUMN secret_ref;

ALTER TABLE iam_organization_miniapp
    ADD CONSTRAINT ck_iam_miniapp_app_secret CHECK (
        app_secret IS NULL
        OR (
            BINARY app_secret = BINARY TRIM(app_secret)
            AND CHAR_LENGTH(app_secret) > 0
        )
    ),
    ADD CONSTRAINT ck_iam_miniapp_login_secret CHECK (
        login_enabled = 0 OR app_secret IS NOT NULL
    );
