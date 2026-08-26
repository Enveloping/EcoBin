-- V58: finish the V57 external-clock boundary and make recovery facts
-- restart-safe.  V57 is already deployed and immutable, so all corrections
-- are forward-only.

-- One physical command can accumulate more than one qualified terminal fact.
-- For example, a clean window first reaches FAILED/COMMAND_EXPIRED and a
-- later edge restart reaches FAILED/EDGE_RESTARTED.  Keep both immutable
-- observations while making an exact stage + error replay idempotent.  The
-- generated identity maps a wire null errorCode to the stable empty key; the
-- existing result-shape check prevents an empty error on failure stages.
ALTER TABLE dev_device_command_event
    DROP INDEX uq_dev_command_event_stage,
    ADD COLUMN observation_error_identity VARCHAR(64)
        CHARACTER SET ascii COLLATE ascii_bin
        GENERATED ALWAYS AS (COALESCE(error_code, '')) STORED,
    ADD CONSTRAINT uq_dev_command_event_stage_error
        UNIQUE (
            command_id,
            observation_stage,
            observation_error_identity
        );

-- Remove the two authorization checks before repairing rows written by the
-- V57 fallback.  During an uncertain create/query recovery, old application
-- code copied backend created_at into channel_created_at even though WeChat
-- did not return create_time.  Exact equality with the backend timestamp plus
-- absence of any observed provider create time identifies that compatibility
-- value, including the case where the query did recover a display package.
ALTER TABLE fund_wechat_transfer_authorization
    DROP CHECK ck_fund_transfer_authorization_state,
    DROP CHECK ck_fund_transfer_authorization_times;

UPDATE fund_wechat_transfer_authorization authorization_row
SET authorization_row.channel_created_at = NULL
WHERE authorization_row.channel_created_at = authorization_row.created_at
  AND NOT EXISTS (
      SELECT 1
      FROM fund_wechat_transfer_authorization_observation observation_row
      WHERE observation_row.transfer_authorization_id = authorization_row.id
        AND observation_row.observed_channel_created_at IS NOT NULL
        AND observation_row.observed_channel_created_at =
            authorization_row.channel_created_at
  );

-- Older application versions never closed the display-package recovery issue
-- after the authorization reached a state where that package was recovered or
-- no longer needed.  Resolve those durable false positives during upgrade so
-- already-terminal rows do not depend on another channel event to converge.
UPDATE ops_reconciliation_issue recovery_issue
JOIN fund_wechat_transfer_authorization authorization_row
  ON recovery_issue.scope_kind = 'ORGANIZATION'
 AND recovery_issue.tenant_id = authorization_row.tenant_id
 AND recovery_issue.organization_id = authorization_row.organization_id
 AND recovery_issue.subject_type = 'WECHAT_TRANSFER_AUTHORIZATION'
 AND recovery_issue.subject_stable_key =
     authorization_row.out_authorization_no
SET recovery_issue.state = 'RESOLVED',
    recovery_issue.system_verified_resolved_at = GREATEST(
        recovery_issue.last_seen_at,
        recovery_issue.updated_at,
        authorization_row.updated_at,
        UTC_TIMESTAMP(3)
    ),
    recovery_issue.lock_version = recovery_issue.lock_version + 1,
    recovery_issue.updated_at = GREATEST(
        recovery_issue.last_seen_at,
        recovery_issue.updated_at,
        authorization_row.updated_at,
        UTC_TIMESTAMP(3)
    )
WHERE recovery_issue.issue_code =
      'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_QUERY_RECOVERY_MISSING'
  AND recovery_issue.state = 'UNRESOLVED'
  AND (
      authorization_row.local_state IN (
          'CREATE_REJECTED', 'CLOSED', 'EXPIRED'
      )
      OR (
          authorization_row.local_state = 'ACTIVE'
          AND LEFT(
              recovery_issue.redacted_evidence_summary,
              CHAR_LENGTH(
                  'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE;'
              )
          ) = 'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE;'
      )
      OR (
          authorization_row.local_state = 'WAIT_USER_CONFIRM'
          AND authorization_row.package_info IS NOT NULL
          AND authorization_row.package_expires_at IS NOT NULL
          AND authorization_row.package_expires_at > UTC_TIMESTAMP(3)
          AND LEFT(
              recovery_issue.redacted_evidence_summary,
              CHAR_LENGTH(
                  'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE;'
              )
          ) = 'reason=WAITING_DISPLAY_PACKAGE_UNRECOVERABLE;'
      )
  );

-- An UNKNOWN_STATE issue describes uncertainty about the provider state, not
-- a permanent operator finding.  Once the durable authorization projection
-- contains a known state, old unresolved issues must converge as well.
UPDATE ops_reconciliation_issue unknown_issue
JOIN fund_wechat_transfer_authorization authorization_row
  ON unknown_issue.scope_kind = 'ORGANIZATION'
 AND unknown_issue.tenant_id = authorization_row.tenant_id
 AND unknown_issue.organization_id = authorization_row.organization_id
 AND unknown_issue.subject_type = 'WECHAT_TRANSFER_AUTHORIZATION'
 AND unknown_issue.subject_stable_key =
     authorization_row.out_authorization_no
SET unknown_issue.state = 'RESOLVED',
    unknown_issue.system_verified_resolved_at = GREATEST(
        unknown_issue.last_seen_at,
        unknown_issue.updated_at,
        authorization_row.updated_at,
        UTC_TIMESTAMP(3)
    ),
    unknown_issue.lock_version = unknown_issue.lock_version + 1,
    unknown_issue.updated_at = GREATEST(
        unknown_issue.last_seen_at,
        unknown_issue.updated_at,
        authorization_row.updated_at,
        UTC_TIMESTAMP(3)
    )
WHERE unknown_issue.issue_code =
      'FUNDS.MERCHANT_TRANSFER_AUTHORIZATION_UNKNOWN_STATE'
  AND unknown_issue.state = 'UNRESOLVED'
  AND authorization_row.local_state IN (
      'CREATE_REJECTED', 'WAIT_USER_CONFIRM', 'ACTIVE', 'CLOSED', 'EXPIRED'
  );

-- channel_created_at is raw provider evidence and may legitimately be absent
-- from a signed query response.  The confirmation projection uses a backend
-- timestamp when provider creation time is unavailable; existing rows whose
-- deadline was derived from a real provider create_time remain legal.
ALTER TABLE fund_wechat_transfer_authorization
    ADD CONSTRAINT ck_fund_transfer_authorization_state CHECK (
        local_state IN (
            'CREATED',
            'CREATE_REJECTED',
            'WAIT_USER_CONFIRM',
            'ACTIVE',
            'CLOSED',
            'EXPIRED',
            'UNKNOWN'
        )
        AND (
            (
                local_state = 'CREATED'
                AND channel_state IS NULL
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND channel_created_at IS NULL
                AND confirmation_deadline_at IS NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'CREATE_REJECTED'
                AND channel_state IS NULL
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND last_api_error_code IS NOT NULL
                AND channel_created_at IS NULL
                AND confirmation_deadline_at IS NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'WAIT_USER_CONFIRM'
                AND channel_state IS NOT NULL
                AND channel_state = 'WAIT_USER_CONFIRM'
                AND authorization_id IS NULL
                AND package_info IS NOT NULL
                AND package_expires_at IS NOT NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'ACTIVE'
                AND channel_state IS NOT NULL
                AND channel_state = 'TAKING_EFFECT'
                AND authorization_id IS NOT NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NOT NULL
                AND closed_at IS NULL
                AND close_reason IS NULL
            )
            OR
            (
                local_state = 'CLOSED'
                AND channel_state IS NOT NULL
                AND channel_state = 'CLOSED'
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND confirmation_deadline_at IS NOT NULL
                AND closed_at IS NOT NULL
                AND close_reason IS NOT NULL
            )
            OR
            (
                local_state = 'EXPIRED'
                AND channel_state IS NOT NULL
                AND channel_state = 'WAIT_USER_CONFIRM'
                AND last_api_error_code IS NOT NULL
                AND last_api_error_code = 'NOT_FOUND'
                AND authorization_id IS NULL
                AND package_info IS NULL
                AND package_expires_at IS NULL
                AND confirmation_deadline_at IS NOT NULL
                AND authorized_at IS NULL
                AND closed_at IS NOT NULL
                AND close_reason IS NOT NULL
                AND close_reason =
                    'USER_OVERDUE_UNCONFIRMED_AFTER_RETENTION'
                AND closed_at >= DATE_ADD(
                    confirmation_deadline_at,
                    INTERVAL 30 DAY
                )
            )
            OR local_state = 'UNKNOWN'
        )
        AND (
            (package_info IS NULL AND package_expires_at IS NULL)
            OR (package_info IS NOT NULL AND package_expires_at IS NOT NULL)
        )
    ),
    ADD CONSTRAINT ck_fund_transfer_authorization_times CHECK (
        updated_at >= created_at
        AND (submitted_at IS NULL OR submitted_at >= created_at)
        AND (
            confirmation_deadline_at IS NULL
            OR confirmation_deadline_at = DATE_ADD(
                COALESCE(submitted_at, created_at),
                INTERVAL 24 HOUR
            )
            OR confirmation_deadline_at = DATE_ADD(
                created_at,
                INTERVAL 24 HOUR
            )
            OR (
                channel_created_at IS NOT NULL
                AND confirmation_deadline_at = DATE_ADD(
                    channel_created_at,
                    INTERVAL 24 HOUR
                )
            )
        )
    );

-- A V56 process could finish a schema-v1 seal after V57's one-time backfill.
-- That legacy shape always has both device completion timestamps, so it is the
-- only NULL-quality terminal shape that can be upgraded unambiguously.
UPDATE dev_factory_seal_authorization
SET completion_clock_quality = 'SYNCED'
WHERE authorization_status = 'SEALED'
  AND completion_clock_quality IS NULL
  AND sealed_at IS NOT NULL
  AND cleanup_completed_at IS NOT NULL;

-- MySQL CHECK constraints accept UNKNOWN, so IN (...) alone does not reject
-- NULL.  Require an explicit quality fact for every irreversible SEALED row.
ALTER TABLE dev_factory_seal_authorization
    DROP CHECK ck_dev_factory_seal_status,
    ADD CONSTRAINT ck_dev_factory_seal_status CHECK (
        (
            authorization_status = 'PENDING'
            AND acknowledged_at IS NULL
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND completion_clock_quality IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'ACKNOWLEDGED'
            AND acknowledged_at IS NOT NULL
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND completion_clock_quality IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'CANCELLED'
            AND acknowledged_at IS NULL
            AND cancelled_at IS NOT NULL
            AND cancellation_reason IS NOT NULL
            AND cancellation_reason REGEXP '^[A-Z][A-Z0-9_]{0,63}$'
            AND completion_event_uid IS NULL
            AND completion_payload_sha256 IS NULL
            AND image_release_id IS NULL
            AND image_release_sha256 IS NULL
            AND factory_report_sha256 IS NULL
            AND authorization_binding_sha256 IS NULL
            AND operator_confirmation_uid IS NULL
            AND completion_clock_quality IS NULL
            AND sealed_at IS NULL
            AND cleanup_completed_at IS NULL
            AND completion_received_at IS NULL
        )
        OR (
            authorization_status = 'SEALED'
            AND cancelled_at IS NULL
            AND cancellation_reason IS NULL
            AND completion_event_uid IS NOT NULL
            AND completion_payload_sha256 IS NOT NULL
            AND image_release_id IS NOT NULL
            AND CHAR_LENGTH(image_release_id) BETWEEN 1 AND 128
            AND image_release_id REGEXP '^[!-~]+$'
            AND image_release_sha256 IS NOT NULL
            AND factory_report_sha256 IS NOT NULL
            AND authorization_binding_sha256 IS NOT NULL
            AND operator_confirmation_uid IS NOT NULL
            AND completion_clock_quality IS NOT NULL
            AND completion_clock_quality IN (
                'SYNCED', 'ESTIMATED', 'UNAVAILABLE'
            )
            AND (
                (
                    completion_clock_quality = 'SYNCED'
                    AND sealed_at IS NOT NULL
                    AND cleanup_completed_at IS NOT NULL
                )
                OR (
                    completion_clock_quality IN ('ESTIMATED', 'UNAVAILABLE')
                    AND sealed_at IS NULL
                    AND cleanup_completed_at IS NULL
                )
            )
            AND completion_received_at IS NOT NULL
        )
    );
