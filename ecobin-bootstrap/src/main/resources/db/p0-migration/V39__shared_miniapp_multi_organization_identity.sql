-- Replace the organization-owned AppID/OpenID model with one platform
-- mini-program channel, channel-scoped WeChat subjects, and independent
-- organization accounts. V39 is a forward-only cutover: runtime code must not
-- read or dual-write the V38 organization-miniapp shape.

DROP TRIGGER IF EXISTS trg_iam_miniapp_activation_immutable;
DROP TRIGGER IF EXISTS trg_iam_org_user_registration_immutable;

-- Drop only foreign keys whose shape contains the retired column/table. The
-- stable tenant/organization/user foreign keys that do not contain it remain.
DELIMITER $$

CREATE PROCEDURE p0_v39_drop_retired_miniapp_foreign_keys()
BEGIN
    DECLARE done INT DEFAULT 0;
    DECLARE statement_text LONGTEXT;
    DECLARE statements CURSOR FOR
        SELECT DISTINCT CONCAT(
            'ALTER TABLE `', k.table_name,
            '` DROP FOREIGN KEY `', k.constraint_name, '`'
        )
        FROM information_schema.key_column_usage k
        WHERE k.constraint_schema = DATABASE()
          AND k.referenced_table_name IS NOT NULL
          AND (
              k.referenced_table_name = 'iam_organization_miniapp'
              OR k.column_name = 'organization_miniapp_id'
              OR k.referenced_column_name IN (
                  'organization_miniapp_id', 'openid'
              )
          );
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = 1;

    OPEN statements;
    drop_loop: LOOP
        FETCH statements INTO statement_text;
        IF done = 1 THEN
            LEAVE drop_loop;
        END IF;
        SET @p0_v39_sql = statement_text;
        PREPARE p0_v39_statement FROM @p0_v39_sql;
        EXECUTE p0_v39_statement;
        DEALLOCATE PREPARE p0_v39_statement;
    END LOOP;
    CLOSE statements;
END$$

CALL p0_v39_drop_retired_miniapp_foreign_keys()$$
DROP PROCEDURE p0_v39_drop_retired_miniapp_foreign_keys$$

DELIMITER ;

ALTER TABLE iam_organization_miniapp
    DROP FOREIGN KEY fk_iam_miniapp_org,
    DROP INDEX uq_iam_miniapp_org,
    DROP INDEX uq_iam_miniapp_scope_id,
    DROP INDEX uq_iam_miniapp_funds_ref,
    DROP INDEX ix_iam_miniapp_scope;

RENAME TABLE iam_organization_miniapp TO iam_miniapp_channel;

ALTER TABLE iam_miniapp_channel
    ADD COLUMN channel_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER id,
    ADD COLUMN entry_base_url VARCHAR(512)
        CHARACTER SET ascii COLLATE ascii_bin NULL AFTER app_secret;

UPDATE iam_miniapp_channel
SET channel_uid = LOWER(CONCAT(
        SUBSTRING(MD5(CONCAT('ecobin:miniapp-channel:', id, ':', appid)), 1, 8),
        '-',
        SUBSTRING(MD5(CONCAT('ecobin:miniapp-channel:', id, ':', appid)), 9, 4),
        '-4',
        SUBSTRING(MD5(CONCAT('ecobin:miniapp-channel:', id, ':', appid)), 14, 3),
        '-8',
        SUBSTRING(MD5(CONCAT('ecobin:miniapp-channel:', id, ':', appid)), 18, 3),
        '-',
        SUBSTRING(MD5(CONCAT('ecobin:miniapp-channel:', id, ':', appid)), 21, 12)
    ));

ALTER TABLE iam_miniapp_channel
    MODIFY COLUMN channel_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    MODIFY COLUMN app_secret VARCHAR(256)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        COMMENT '平台小程序渠道 AppSecret；日志和审计必须脱敏',
    ADD CONSTRAINT uq_iam_channel_uid UNIQUE (channel_uid),
    ADD CONSTRAINT uq_iam_channel_id_appid UNIQUE (id, appid),
    ADD CONSTRAINT ck_iam_channel_uid_v4 CHECK (
        channel_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT ck_iam_channel_entry_url CHECK (
        entry_base_url IS NULL
        OR (
            BINARY entry_base_url = BINARY TRIM(entry_base_url)
            AND entry_base_url REGEXP '^https://[^#]+$'
        )
    );

CREATE TABLE iam_organization_miniapp_binding (
    id BIGINT NOT NULL AUTO_INCREMENT,
    binding_uid CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    tenant_id BIGINT NOT NULL,
    organization_id BIGINT NOT NULL,
    miniapp_channel_id BIGINT NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bound_at DATETIME(3) NOT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_org_channel_binding_uid UNIQUE (binding_uid),
    CONSTRAINT uq_iam_org_channel_binding_org
        UNIQUE (tenant_id, organization_id),
    CONSTRAINT uq_iam_org_channel_binding_scope
        UNIQUE (tenant_id, organization_id, miniapp_channel_id),
    CONSTRAINT ck_iam_org_channel_binding_uid_v4 CHECK (
        binding_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_org_channel_binding_status CHECK (
        status IN ('ACTIVE', 'DISABLED')
    ),
    CONSTRAINT ck_iam_org_channel_binding_version
        CHECK (lock_version >= 0),
    CONSTRAINT ck_iam_org_channel_binding_times CHECK (
        bound_at >= created_at AND updated_at >= created_at
    ),
    CONSTRAINT fk_iam_org_channel_binding_org
        FOREIGN KEY (tenant_id, organization_id)
        REFERENCES iam_organization (tenant_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_iam_org_channel_binding_channel
        FOREIGN KEY (miniapp_channel_id)
        REFERENCES iam_miniapp_channel (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_org_channel_binding_channel (
        miniapp_channel_id, status, tenant_id, organization_id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO iam_organization_miniapp_binding (
    binding_uid, tenant_id, organization_id, miniapp_channel_id,
    status, bound_at, lock_version, created_at, updated_at
)
SELECT LOWER(CONCAT(
           SUBSTRING(MD5(CONCAT('ecobin:org-channel-binding:', id)), 1, 8),
           '-',
           SUBSTRING(MD5(CONCAT('ecobin:org-channel-binding:', id)), 9, 4),
           '-4',
           SUBSTRING(MD5(CONCAT('ecobin:org-channel-binding:', id)), 14, 3),
           '-8',
           SUBSTRING(MD5(CONCAT('ecobin:org-channel-binding:', id)), 18, 3),
           '-',
           SUBSTRING(MD5(CONCAT('ecobin:org-channel-binding:', id)), 21, 12)
       )),
       tenant_id, organization_id, id, 'ACTIVE', configured_at,
       0, created_at, updated_at
FROM iam_miniapp_channel;

ALTER TABLE iam_miniapp_channel
    DROP COLUMN tenant_id,
    DROP COLUMN organization_id,
    ADD INDEX ix_iam_channel_login (login_enabled, activated_at, id);

CREATE TABLE iam_wechat_subject (
    id BIGINT NOT NULL AUTO_INCREMENT,
    wechat_subject_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    miniapp_channel_id BIGINT NOT NULL,
    openid VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    auth_version BIGINT NOT NULL DEFAULT 0,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_iam_wechat_subject_uid UNIQUE (wechat_subject_uid),
    CONSTRAINT uq_iam_wechat_subject_channel_openid
        UNIQUE (miniapp_channel_id, openid),
    CONSTRAINT uq_iam_wechat_subject_channel_id
        UNIQUE (miniapp_channel_id, id),
    CONSTRAINT ck_iam_wechat_subject_uid_v4 CHECK (
        wechat_subject_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    CONSTRAINT ck_iam_wechat_subject_openid CHECK (
        BINARY openid = BINARY TRIM(openid)
        AND CHAR_LENGTH(TRIM(openid)) > 0
    ),
    CONSTRAINT ck_iam_wechat_subject_status CHECK (
        status IN ('ACTIVE', 'FROZEN')
    ),
    CONSTRAINT ck_iam_wechat_subject_versions CHECK (
        auth_version >= 0 AND lock_version >= 0
    ),
    CONSTRAINT ck_iam_wechat_subject_times CHECK (
        updated_at >= created_at
    ),
    CONSTRAINT fk_iam_wechat_subject_channel
        FOREIGN KEY (miniapp_channel_id)
        REFERENCES iam_miniapp_channel (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_iam_wechat_subject_status (
        miniapp_channel_id, status, id
    )
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- All scoped tables now refer to the platform channel, not an organization
-- miniapp aggregate. Column renames preserve immutable financial snapshots.
ALTER TABLE iam_staff_login_session
    DROP CHECK ck_iam_staff_session_client_shape;

ALTER TABLE iam_organization_user
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE iam_staff_miniapp_binding
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE iam_staff_login_session
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE iam_organization_user_session
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE fund_miniapp_merchant_binding
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE fund_wechat_payment
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE fund_withdrawal_order
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE fund_wechat_transfer
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;
ALTER TABLE fund_wechat_transfer_authorization
    RENAME COLUMN organization_miniapp_id TO miniapp_channel_id;

INSERT INTO iam_wechat_subject (
    wechat_subject_uid, miniapp_channel_id, openid, status,
    auth_version, lock_version, created_at, updated_at
)
SELECT LOWER(CONCAT(
           SUBSTRING(MD5(CONCAT(
               'ecobin:wechat-subject:', miniapp_channel_id, ':', openid
           )), 1, 8),
           '-',
           SUBSTRING(MD5(CONCAT(
               'ecobin:wechat-subject:', miniapp_channel_id, ':', openid
           )), 9, 4),
           '-4',
           SUBSTRING(MD5(CONCAT(
               'ecobin:wechat-subject:', miniapp_channel_id, ':', openid
           )), 14, 3),
           '-8',
           SUBSTRING(MD5(CONCAT(
               'ecobin:wechat-subject:', miniapp_channel_id, ':', openid
           )), 18, 3),
           '-',
           SUBSTRING(MD5(CONCAT(
               'ecobin:wechat-subject:', miniapp_channel_id, ':', openid
           )), 21, 12)
       )),
       miniapp_channel_id, openid, 'ACTIVE', 0, 0,
       MIN(created_at), MAX(updated_at)
FROM iam_organization_user
GROUP BY miniapp_channel_id, openid;

ALTER TABLE iam_organization_user
    ADD COLUMN wechat_subject_id BIGINT NULL AFTER miniapp_channel_id;

UPDATE iam_organization_user u
JOIN iam_wechat_subject s
  ON s.miniapp_channel_id = u.miniapp_channel_id
 AND s.openid = u.openid
SET u.wechat_subject_id = s.id;

ALTER TABLE fund_wechat_transfer_authorization
    ADD COLUMN wechat_subject_id BIGINT NULL
        AFTER organization_user_id;

UPDATE fund_wechat_transfer_authorization a
JOIN iam_organization_user u
  ON u.tenant_id = a.tenant_id
 AND u.organization_id = a.organization_id
 AND u.id = a.organization_user_id
SET a.wechat_subject_id = u.wechat_subject_id;

ALTER TABLE iam_organization_user
    MODIFY COLUMN wechat_subject_id BIGINT NOT NULL,
    DROP CHECK ck_iam_org_user_openid_nonblank,
    DROP INDEX uq_iam_org_user_appid_openid,
    DROP INDEX uq_iam_org_user_scope_app_id,
    DROP INDEX uq_iam_org_user_funds_identity_ref,
    DROP INDEX uq_iam_org_user_transfer_authorization_ref,
    DROP COLUMN openid,
    ADD CONSTRAINT uq_iam_org_user_subject_scope
        UNIQUE (wechat_subject_id, tenant_id, organization_id),
    ADD CONSTRAINT uq_iam_org_user_channel_scope_id
        UNIQUE (
            tenant_id, organization_id, miniapp_channel_id, id
        ),
    ADD CONSTRAINT uq_iam_org_user_selected_session_ref
        UNIQUE (
            tenant_id, organization_id, miniapp_channel_id,
            wechat_subject_id, id
        ),
    ADD CONSTRAINT fk_iam_org_user_channel_binding
        FOREIGN KEY (tenant_id, organization_id, miniapp_channel_id)
        REFERENCES iam_organization_miniapp_binding (
            tenant_id, organization_id, miniapp_channel_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_iam_org_user_wechat_subject
        FOREIGN KEY (miniapp_channel_id, wechat_subject_id)
        REFERENCES iam_wechat_subject (miniapp_channel_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_iam_org_user_subject_recent (
        wechat_subject_id, status, registered_at DESC, id DESC
    );

ALTER TABLE fund_wechat_transfer_authorization
    MODIFY COLUMN wechat_subject_id BIGINT NOT NULL,
    DROP INDEX uq_fund_transfer_authorization_current,
    ADD CONSTRAINT uq_fund_transfer_authorization_current UNIQUE (
        merchant_profile_id,
        miniapp_channel_id,
        wechat_subject_id,
        scene_id_snapshot,
        current_authorization_slot
    ),
    ADD CONSTRAINT fk_fund_transfer_authorization_subject
        FOREIGN KEY (miniapp_channel_id, wechat_subject_id)
        REFERENCES iam_wechat_subject (miniapp_channel_id, id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;

-- Existing V38 mini-program tokens refer to the retired identity shape.
-- Revoke them before adding the new subject snapshot to sessions.
UPDATE iam_organization_user_session
SET revoked_at = COALESCE(revoked_at, UTC_TIMESTAMP(3)),
    revocation_reason = COALESCE(
        revocation_reason, 'V39_IDENTITY_MODEL_CHANGED'
    )
WHERE revoked_at IS NULL;

UPDATE iam_staff_login_session
SET revoked_at = COALESCE(revoked_at, UTC_TIMESTAMP(3)),
    revocation_reason = COALESCE(
        revocation_reason, 'V39_IDENTITY_MODEL_CHANGED'
    )
WHERE client_kind = 'MINIAPP_MANAGEMENT'
  AND revoked_at IS NULL;

ALTER TABLE iam_organization_user_session
    ADD COLUMN wechat_subject_id BIGINT NULL
        AFTER miniapp_channel_id,
    ADD COLUMN selection_operation_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER organization_user_id;

UPDATE iam_organization_user_session s
JOIN iam_organization_user u
  ON u.tenant_id = s.tenant_id
 AND u.organization_id = s.organization_id
 AND u.miniapp_channel_id = s.miniapp_channel_id
 AND u.id = s.organization_user_id
SET s.wechat_subject_id = u.wechat_subject_id;

ALTER TABLE iam_organization_user_session
    MODIFY COLUMN wechat_subject_id BIGINT NOT NULL,
    ADD CONSTRAINT uq_iam_org_user_session_selection
        UNIQUE (selection_operation_uid),
    ADD CONSTRAINT ck_iam_org_user_session_selection_uid CHECK (
        selection_operation_uid IS NULL
        OR selection_operation_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT fk_iam_org_user_session_user_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_channel_id,
            wechat_subject_id, organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id, organization_id, miniapp_channel_id,
            wechat_subject_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_iam_org_user_session_channel_binding
        FOREIGN KEY (tenant_id, organization_id, miniapp_channel_id)
        REFERENCES iam_organization_miniapp_binding (
            tenant_id, organization_id, miniapp_channel_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE iam_staff_miniapp_binding
    ADD CONSTRAINT fk_iam_staff_binding_user_v39
        FOREIGN KEY (
            tenant_id, organization_id,
            miniapp_channel_id, organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id, organization_id,
            miniapp_channel_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_iam_staff_binding_channel
        FOREIGN KEY (tenant_id, organization_id, miniapp_channel_id)
        REFERENCES iam_organization_miniapp_binding (
            tenant_id, organization_id, miniapp_channel_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE iam_staff_login_session
    ADD CONSTRAINT ck_iam_staff_session_client_shape_v39 CHECK (
        (
            client_kind = 'WEB'
            AND staff_miniapp_binding_id IS NULL
            AND miniapp_channel_id IS NULL
            AND active_organization_id IS NULL
        )
        OR
        (
            client_kind = 'MINIAPP_MANAGEMENT'
            AND staff_miniapp_binding_id IS NOT NULL
            AND miniapp_channel_id IS NOT NULL
            AND active_organization_id IS NOT NULL
        )
    ),
    ADD CONSTRAINT fk_iam_staff_session_binding_v39
        FOREIGN KEY (
            tenant_id, active_organization_id, miniapp_channel_id,
            staff_account_id, staff_miniapp_binding_id
        )
        REFERENCES iam_staff_miniapp_binding (
            tenant_id, organization_id, miniapp_channel_id,
            staff_account_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_miniapp_merchant_binding
    DROP INDEX uq_fund_binding_miniapp,
    DROP INDEX uq_fund_binding_appid,
    ADD CONSTRAINT uq_fund_binding_org_channel
        UNIQUE (tenant_id, organization_id, miniapp_channel_id),
    ADD CONSTRAINT fk_fund_binding_channel_scope
        FOREIGN KEY (tenant_id, organization_id, miniapp_channel_id)
        REFERENCES iam_organization_miniapp_binding (
            tenant_id, organization_id, miniapp_channel_id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_binding_channel_appid
        FOREIGN KEY (miniapp_channel_id, appid)
        REFERENCES iam_miniapp_channel (id, appid)
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_wechat_payment
    ADD CONSTRAINT fk_fund_wechat_payment_binding_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_merchant_binding_id,
            miniapp_channel_id, merchant_profile_id, appid_snapshot
        )
        REFERENCES fund_miniapp_merchant_binding (
            tenant_id, organization_id, id,
            miniapp_channel_id, merchant_profile_id, appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_wechat_transfer_authorization
    ADD CONSTRAINT fk_fund_transfer_authorization_recipient_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_channel_id,
            wechat_subject_id, organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id, organization_id, miniapp_channel_id,
            wechat_subject_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_transfer_authorization_binding_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_merchant_binding_id,
            miniapp_channel_id, merchant_profile_id, appid_snapshot
        )
        REFERENCES fund_miniapp_merchant_binding (
            tenant_id, organization_id, id,
            miniapp_channel_id, merchant_profile_id, appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_withdrawal_order
    ADD CONSTRAINT fk_fund_withdrawal_recipient_identity_v39
        FOREIGN KEY (
            tenant_id, organization_id,
            miniapp_channel_id, organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id, organization_id,
            miniapp_channel_id, id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_withdrawal_binding_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_merchant_binding_id,
            miniapp_channel_id, merchant_profile_id, appid_snapshot
        )
        REFERENCES fund_miniapp_merchant_binding (
            tenant_id, organization_id, id,
            miniapp_channel_id, merchant_profile_id, appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_withdrawal_transfer_authorization_v39
        FOREIGN KEY (transfer_authorization_id)
        REFERENCES fund_wechat_transfer_authorization (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE fund_wechat_transfer
    ADD CONSTRAINT fk_fund_wechat_transfer_binding_v39
        FOREIGN KEY (
            tenant_id, organization_id, miniapp_merchant_binding_id,
            miniapp_channel_id, merchant_profile_id, appid_snapshot
        )
        REFERENCES fund_miniapp_merchant_binding (
            tenant_id, organization_id, id,
            miniapp_channel_id, merchant_profile_id, appid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_wechat_transfer_authorization_v39
        FOREIGN KEY (transfer_authorization_id)
        REFERENCES fund_wechat_transfer_authorization (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_wechat_transfer_withdrawal_v39
        FOREIGN KEY (
            tenant_id, organization_id, withdrawal_order_id, amount_cent,
            merchant_profile_id, miniapp_merchant_binding_id,
            miniapp_channel_id, appid_snapshot, openid_snapshot
        )
        REFERENCES fund_withdrawal_order (
            tenant_id, organization_id, id, amount_cent,
            merchant_profile_id, miniapp_merchant_binding_id,
            miniapp_channel_id, appid_snapshot, openid_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_fund_wechat_transfer_withdrawal_mode_v39
        FOREIGN KEY (
            tenant_id, organization_id, withdrawal_order_id, amount_cent,
            merchant_profile_id, miniapp_merchant_binding_id,
            miniapp_channel_id, appid_snapshot, openid_snapshot,
            collection_mode_snapshot, transfer_authorization_id,
            out_authorization_no_snapshot, authorization_id_snapshot
        )
        REFERENCES fund_withdrawal_order (
            tenant_id, organization_id, id, amount_cent,
            merchant_profile_id, miniapp_merchant_binding_id,
            miniapp_channel_id, appid_snapshot, openid_snapshot,
            collection_mode_snapshot, transfer_authorization_id,
            out_authorization_no_snapshot, authorization_id_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT;

-- Ordinary HTTPS device entry URLs are derived on demand. There is no QR
-- object, asynchronous generator, or READY gate in the device lifecycle.
ALTER TABLE dev_device_asset
    DROP CHECK ck_dev_asset_qr,
    DROP COLUMN miniapp_qr_status,
    DROP COLUMN miniapp_qr_object_key,
    DROP COLUMN miniapp_qr_generated_at;

DELIMITER $$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_iam_channel_activation_immutable
BEFORE UPDATE ON iam_miniapp_channel
FOR EACH ROW
BEGIN
    IF OLD.activated_at IS NOT NULL THEN
        IF NOT (NEW.activated_at <=> OLD.activated_at)
            OR NOT (NEW.appid <=> OLD.appid) THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT =
                    'activated miniapp channel AppID and activation time are immutable';
        END IF;
    END IF;
END$$

CREATE DEFINER = 'ecobin_trigger_definer'@'%'
TRIGGER trg_iam_org_user_registration_immutable
BEFORE UPDATE ON iam_organization_user
FOR EACH ROW
BEGIN
    IF NOT (NEW.tenant_id <=> OLD.tenant_id)
        OR NOT (NEW.organization_id <=> OLD.organization_id)
        OR NOT (NEW.miniapp_channel_id <=> OLD.miniapp_channel_id)
        OR NOT (NEW.wechat_subject_id <=> OLD.wechat_subject_id)
        OR NOT (NEW.registered_at <=> OLD.registered_at)
        OR NOT (
            NEW.registered_via_asset_id
            <=> OLD.registered_via_asset_id
        ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT =
                'organization account identity and registration attribution are immutable';
    END IF;
END$$

DELIMITER ;
