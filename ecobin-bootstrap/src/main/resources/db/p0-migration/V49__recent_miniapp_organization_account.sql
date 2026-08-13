-- V49: remember the most recently signed-in organization account for each
-- channel-scoped WeChat subject. Registration time remains immutable and
-- continues to serve registration attribution and acquisition statistics.

ALTER TABLE iam_organization_user
    ADD COLUMN last_login_at DATETIME(3) NULL
        AFTER registered_at;

-- Preserve the latest successful session fact that is still available. An
-- ordinary/cleaning session points to the organization user directly. A
-- management session reaches the organization user through the immutable
-- staff mini-program binding snapshot.
UPDATE iam_organization_user user_row
LEFT JOIN (
    SELECT tenant_id,
           organization_id,
           organization_user_id,
           MAX(issued_at) AS last_ordinary_login_at
    FROM iam_organization_user_session
    GROUP BY tenant_id, organization_id, organization_user_id
) ordinary_session
  ON ordinary_session.tenant_id = user_row.tenant_id
 AND ordinary_session.organization_id = user_row.organization_id
 AND ordinary_session.organization_user_id = user_row.id
LEFT JOIN (
    SELECT binding.tenant_id,
           binding.organization_id,
           binding.organization_user_id,
           MAX(session_row.issued_at) AS last_management_login_at
    FROM iam_staff_login_session session_row
    JOIN iam_staff_miniapp_binding binding
      ON binding.tenant_id = session_row.tenant_id
     AND binding.organization_id = session_row.active_organization_id
     AND binding.miniapp_channel_id = session_row.miniapp_channel_id
     AND binding.staff_account_id = session_row.staff_account_id
     AND binding.id = session_row.staff_miniapp_binding_id
    WHERE session_row.client_kind = 'MINIAPP_MANAGEMENT'
    GROUP BY binding.tenant_id,
             binding.organization_id,
             binding.organization_user_id
) management_session
  ON management_session.tenant_id = user_row.tenant_id
 AND management_session.organization_id = user_row.organization_id
 AND management_session.organization_user_id = user_row.id
SET user_row.last_login_at = GREATEST(
        user_row.registered_at,
        COALESCE(
            ordinary_session.last_ordinary_login_at,
            user_row.registered_at
        ),
        COALESCE(
            management_session.last_management_login_at,
            user_row.registered_at
        )
    );

ALTER TABLE iam_organization_user
    MODIFY COLUMN last_login_at DATETIME(3) NOT NULL,
    DROP INDEX ix_iam_org_user_subject_recent,
    ADD CONSTRAINT ck_iam_org_user_last_login_v49 CHECK (
        last_login_at >= registered_at
    ),
    ADD INDEX ix_iam_org_user_subject_recent_login (
        wechat_subject_id,
        status,
        last_login_at DESC,
        id DESC
    );
