-- Make OneNet lifecycle notifications the only authoritative transport-presence
-- facts and stop resubmitting physical/configuration commands after OneNet has
-- accepted them.

ALTER TABLE dev_device_transport_state
    DROP CHECK ck_dev_transport_evidence;

UPDATE dev_device_transport_state
SET onenet_connection_status = 'UNKNOWN',
    status_observed_at = NULL,
    status_received_at = NULL,
    evidence_source = NULL,
    source_inbox_id = NULL,
    lock_version = lock_version + 1,
    updated_at = UTC_TIMESTAMP(3);

UPDATE dev_device_transport_state transport
JOIN dev_device_asset asset
  ON asset.id = transport.asset_id
JOIN (
    SELECT ranked.hardware_sn,
           ranked.status,
           ranked.observed_at,
           ranked.received_at,
           ranked.inbox_id
    FROM (
        SELECT
            JSON_UNQUOTE(JSON_EXTRACT(
                inbox.normalized_payload,
                '$.trustedSource.deviceName'
            )) AS hardware_sn,
            JSON_UNQUOTE(JSON_EXTRACT(
                inbox.normalized_payload,
                '$.presence.status'
            )) AS status,
            COALESCE(
                CAST(REPLACE(REPLACE(
                    JSON_UNQUOTE(JSON_EXTRACT(
                        inbox.normalized_payload,
                        '$.presence.observedAt'
                    )), 'T', ' '), 'Z', '') AS DATETIME(3)),
                inbox.first_received_at
            ) AS observed_at,
            inbox.first_received_at AS received_at,
            inbox.id AS inbox_id,
            ROW_NUMBER() OVER (
                PARTITION BY JSON_UNQUOTE(JSON_EXTRACT(
                    inbox.normalized_payload,
                    '$.trustedSource.deviceName'
                ))
                ORDER BY
                    CAST(REPLACE(REPLACE(
                        JSON_UNQUOTE(JSON_EXTRACT(
                            inbox.normalized_payload,
                            '$.presence.observedAt'
                        )), 'T', ' '), 'Z', '') AS DATETIME(3)) DESC,
                    CASE JSON_UNQUOTE(JSON_EXTRACT(
                        inbox.normalized_payload,
                        '$.presence.status'
                    )) WHEN 'OFFLINE' THEN 0 ELSE 1 END,
                    inbox.first_received_at DESC,
                    inbox.id DESC
            ) AS row_no
        FROM ops_inbox_message inbox
        WHERE inbox.message_kind = 'DEVICE_TRANSPORT_STATUS_CHANGED'
          AND inbox.processing_state = 'PROCESSED'
          AND JSON_UNQUOTE(JSON_EXTRACT(
              inbox.normalized_payload,
              '$.presence.status'
          )) IN ('ONLINE', 'OFFLINE')
    ) ranked
    WHERE ranked.row_no = 1
) lifecycle
  ON lifecycle.hardware_sn = asset.hardware_sn
SET transport.onenet_connection_status = lifecycle.status,
    transport.status_observed_at = lifecycle.observed_at,
    transport.status_received_at = lifecycle.received_at,
    transport.evidence_source = 'LIFECYCLE_EVENT',
    transport.source_inbox_id = lifecycle.inbox_id,
    transport.lock_version = transport.lock_version + 1,
    transport.updated_at = UTC_TIMESTAMP(3);

ALTER TABLE dev_device_transport_state
    ADD CONSTRAINT ck_dev_transport_evidence CHECK (
        (
            onenet_connection_status = 'UNKNOWN'
            AND status_observed_at IS NULL
            AND status_received_at IS NULL
            AND evidence_source IS NULL
            AND source_inbox_id IS NULL
        )
        OR
        (
            onenet_connection_status IN ('ONLINE', 'OFFLINE')
            AND status_observed_at IS NOT NULL
            AND status_received_at IS NOT NULL
            AND evidence_source = 'LIFECYCLE_EVENT'
            AND source_inbox_id IS NOT NULL
        )
    );

ALTER TABLE ops_reliable_task
    DROP CHECK ck_ops_task_dispatch_wait;

UPDATE ops_reliable_task
SET dispatch_wait_reason = CASE
        WHEN dispatch_wait_reason = 'RUNTIME_STALE'
        THEN 'RUNTIME_MISSING'
        ELSE dispatch_wait_reason
    END;

-- Commands which were already accepted by OneNet before this migration wait
-- for device evidence instead of being sent again.
UPDATE ops_reliable_task task
JOIN ops_task_attempt attempt
  ON attempt.id = (
      SELECT latest.id
      FROM ops_task_attempt latest
      WHERE latest.task_id = task.id
        AND latest.technical_result = 'TECHNICAL_SUCCESS'
      ORDER BY latest.attempt_no DESC
      LIMIT 1
  )
JOIN dev_device_command command_row
  ON command_row.id = task.source_device_command_id
 AND command_row.tenant_id = task.tenant_id
 AND command_row.organization_id = task.organization_id
 AND command_row.deployment_id = task.source_device_deployment_id
SET task.dispatch_wait_reason = 'AWAITING_DEVICE_EVIDENCE',
    task.next_run_at = CASE
        WHEN task.task_type = 'ENSURE_DEVICE_CONFIGURATION'
        THEN DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 2 MINUTE)
        ELSE GREATEST(
            UTC_TIMESTAMP(3),
            DATE_ADD(
                COALESCE(
                    CAST(REPLACE(REPLACE(
                        JSON_UNQUOTE(JSON_EXTRACT(
                            command_row.semantic_payload,
                            '$.expiresAt'
                        )), 'T', ' '), 'Z', '') AS DATETIME(3)),
                    UTC_TIMESTAMP(3)
                ),
                INTERVAL 30 SECOND
            )
        )
    END,
    task.lease_token = NULL,
    task.lease_worker = NULL,
    task.lease_until = NULL,
    task.lock_version = task.lock_version + 1,
    task.updated_at = UTC_TIMESTAMP(3)
WHERE task.state = 'PENDING'
  AND task.execution_lane = 'DEVICE'
  AND task.task_category = 'BUSINESS_INTENT'
  AND task.task_type NOT IN (
      'CONFIRM_EDGE_EVENT',
      'PROVIDE_PHOTO_UPLOAD_GRANT'
  );

UPDATE dev_deployment_runtime_state runtime
JOIN dev_device_deployment deployment
  ON deployment.id = runtime.deployment_id
LEFT JOIN dev_device_transport_state transport
  ON transport.asset_id = deployment.asset_id
SET runtime.edge_connection_status = CASE
        WHEN transport.onenet_connection_status = 'OFFLINE'
        THEN 'OFFLINE'
        WHEN transport.onenet_connection_status = 'ONLINE'
             AND runtime.trusted_runtime_received_at IS NOT NULL
        THEN 'ONLINE'
        ELSE 'UNKNOWN'
    END,
    runtime.lock_version = runtime.lock_version + 1,
    runtime.updated_at = UTC_TIMESTAMP(3);

UPDATE ops_reliable_task task
JOIN dev_device_deployment deployment
  ON deployment.id = task.source_device_deployment_id
LEFT JOIN dev_device_transport_state transport
  ON transport.asset_id = deployment.asset_id
LEFT JOIN dev_deployment_runtime_state runtime
  ON runtime.deployment_id = deployment.id
LEFT JOIN dev_config_version config
  ON config.id = (
      SELECT latest.id
      FROM dev_config_version latest
      WHERE latest.deployment_id = deployment.id
      ORDER BY latest.version_no DESC
      LIMIT 1
  )
SET task.dispatch_wait_reason = CASE
        WHEN task.dispatch_wait_reason = 'AWAITING_DEVICE_EVIDENCE'
        THEN 'AWAITING_DEVICE_EVIDENCE'
        WHEN COALESCE(transport.onenet_connection_status, 'UNKNOWN') =
            'OFFLINE'
        THEN 'DEVICE_OFFLINE'
        WHEN COALESCE(transport.onenet_connection_status, 'UNKNOWN') =
            'UNKNOWN'
        THEN 'DEVICE_PRESENCE_UNKNOWN'
        WHEN task.task_type IN (
            'START_DELIVERY_SESSION',
            'START_CLEAN_OPERATION',
            'END_CLEAN_BEFORE_UNLOCK',
            'RESUME_CLEAN_OPERATION',
            'SAMPLE_FULLNESS',
            'MEASURE_EMPTY_BAG_BASELINE'
        ) AND (
            runtime.trusted_runtime_received_at IS NULL
            OR config.id IS NULL
        )
        THEN 'RUNTIME_MISSING'
        ELSE NULL
    END,
    task.lock_version = task.lock_version + 1,
    task.updated_at = UTC_TIMESTAMP(3)
WHERE task.state = 'PENDING'
  AND task.execution_lane = 'DEVICE'
  AND task.task_category = 'BUSINESS_INTENT';

ALTER TABLE ops_reliable_task
    ADD CONSTRAINT ck_ops_task_dispatch_wait CHECK (
        (
            state = 'PENDING'
            AND (
                dispatch_wait_reason IS NULL
                OR dispatch_wait_reason IN (
                    'DEVICE_OFFLINE',
                    'DEVICE_PRESENCE_UNKNOWN',
                    'RUNTIME_MISSING',
                    'AWAITING_DEVICE_EVIDENCE'
                )
            )
        )
        OR
        (
            state <> 'PENDING'
            AND dispatch_wait_reason IS NULL
        )
    ),
    ADD INDEX ix_ops_task_evidence_deadline (
        state,
        execution_lane,
        dispatch_wait_reason,
        next_run_at,
        id
    );

-- Retention scans use these indexes and delete only unreferenced telemetry.
ALTER TABLE ops_inbox_message
    ADD INDEX ix_ops_inbox_telemetry_retention (
        message_kind,
        processing_state,
        processed_at,
        id
    );
