-- Separate OneNet transport presence from deployment business readiness.
-- Temporary dispatch waits remain PENDING and must never masquerade as
-- operator-actionable BLOCKED tasks.

CREATE TABLE dev_device_transport_state (
    asset_id BIGINT NOT NULL,
    onenet_connection_status
        VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status_observed_at DATETIME(3) NULL,
    status_received_at DATETIME(3) NULL,
    evidence_source
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    source_inbox_id BIGINT NULL,
    lock_version BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (asset_id),
    CONSTRAINT ck_dev_transport_status CHECK (
        onenet_connection_status IN ('ONLINE', 'OFFLINE', 'UNKNOWN')
    ),
    CONSTRAINT ck_dev_transport_evidence CHECK (
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
            AND evidence_source IN (
                'LIFECYCLE_EVENT',
                'DEVICE_MESSAGE',
                'OUTBOUND_RESPONSE',
                'LEGACY_RUNTIME_SNAPSHOT'
            )
            AND (
                (
                    evidence_source IN (
                        'LIFECYCLE_EVENT',
                        'DEVICE_MESSAGE'
                    )
                    AND source_inbox_id IS NOT NULL
                )
                OR
                (
                    evidence_source IN (
                        'OUTBOUND_RESPONSE',
                        'LEGACY_RUNTIME_SNAPSHOT'
                    )
                    AND source_inbox_id IS NULL
                )
            )
        )
    ),
    CONSTRAINT ck_dev_transport_lock CHECK (lock_version >= 0),
    CONSTRAINT ck_dev_transport_times CHECK (updated_at >= created_at),
    CONSTRAINT fk_dev_transport_asset
        FOREIGN KEY (asset_id)
        REFERENCES dev_device_asset (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_dev_transport_inbox
        FOREIGN KEY (source_inbox_id)
        REFERENCES ops_inbox_message (id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    INDEX ix_dev_transport_status (
        onenet_connection_status,
        status_observed_at,
        asset_id
    ),
    INDEX ix_dev_transport_inbox (source_inbox_id)
) ENGINE=InnoDB DEFAULT CHARACTER SET=utf8mb4
    COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO dev_device_transport_state (
    asset_id,
    onenet_connection_status,
    status_observed_at,
    status_received_at,
    evidence_source,
    source_inbox_id,
    lock_version,
    created_at,
    updated_at
)
SELECT
    asset.id,
    CASE
        WHEN MAX(runtime.trusted_runtime_received_at) IS NULL
        THEN 'UNKNOWN'
        ELSE 'ONLINE'
    END,
    MAX(runtime.trusted_runtime_received_at),
    MAX(runtime.trusted_runtime_received_at),
    CASE
        WHEN MAX(runtime.trusted_runtime_received_at) IS NULL
        THEN NULL
        ELSE 'LEGACY_RUNTIME_SNAPSHOT'
    END,
    NULL,
    0,
    asset.created_at,
    UTC_TIMESTAMP(3)
FROM dev_device_asset asset
LEFT JOIN dev_device_deployment deployment
  ON deployment.asset_id = asset.id
LEFT JOIN dev_deployment_runtime_state runtime
  ON runtime.deployment_id = deployment.id
GROUP BY asset.id, asset.created_at;

ALTER TABLE ops_reliable_task
    ADD COLUMN dispatch_wait_reason
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER blocked_diagnostic,
    ADD CONSTRAINT ck_ops_task_dispatch_wait CHECK (
        (
            state = 'PENDING'
            AND (
                dispatch_wait_reason IS NULL
                OR dispatch_wait_reason IN (
                    'DEVICE_OFFLINE',
                    'DEVICE_PRESENCE_UNKNOWN',
                    'RUNTIME_STALE'
                )
            )
        )
        OR
        (
            state <> 'PENDING'
            AND dispatch_wait_reason IS NULL
        )
    ),
    ADD INDEX ix_ops_task_device_dispatch (
        state,
        execution_lane,
        dispatch_wait_reason,
        claimable_at,
        priority,
        id
    ),
    ADD INDEX ix_ops_task_deployment_dispatch (
        source_device_deployment_id,
        state,
        dispatch_wait_reason,
        id
    );

ALTER TABLE ops_task_attempt
    DROP CHECK ck_ops_attempt_result,
    ADD CONSTRAINT ck_ops_attempt_result CHECK (
        technical_result IS NULL
        OR technical_result IN (
            'TECHNICAL_SUCCESS',
            'NO_ACTION_REQUIRED',
            'RETRYABLE_FAILURE',
            'OUTCOME_UNKNOWN',
            'PERMANENT_TECHNICAL_FAILURE',
            'TARGET_OFFLINE',
            'TARGET_NOT_FOUND'
        )
    );

-- Existing command tasks are reconciled conservatively. Configuration is the
-- only command allowed to probe an UNKNOWN asset during commissioning.
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
        WHEN COALESCE(
            transport.onenet_connection_status,
            'UNKNOWN'
        ) = 'OFFLINE'
        THEN 'DEVICE_OFFLINE'
        WHEN COALESCE(
            transport.onenet_connection_status,
            'UNKNOWN'
        ) = 'UNKNOWN'
             AND task.task_type <> 'ENSURE_DEVICE_CONFIGURATION'
        THEN 'DEVICE_PRESENCE_UNKNOWN'
        WHEN task.task_type IN (
            'START_DELIVERY_SESSION',
            'START_CLEAN_OPERATION',
            'END_CLEAN_BEFORE_UNLOCK',
            'RESUME_CLEAN_OPERATION',
            'SAMPLE_FULLNESS',
            'MEASURE_EMPTY_BAG_BASELINE'
        )
        AND (
            runtime.trusted_runtime_received_at IS NULL
            OR config.id IS NULL
            OR TIMESTAMPDIFF(
                MICROSECOND,
                runtime.trusted_runtime_received_at,
                UTC_TIMESTAMP(3)
            ) NOT BETWEEN 0 AND LEAST(
                CAST(config.edge_heartbeat_interval_ms AS DECIMAL(30, 0))
                    * CAST(config.edge_heartbeat_miss_threshold AS DECIMAL(30, 0))
                    * CAST(1000 AS DECIMAL(30, 0)),
                CAST(86400000000 AS DECIMAL(30, 0))
            )
        )
        THEN 'RUNTIME_STALE'
        ELSE NULL
    END,
    task.updated_at = UTC_TIMESTAMP(3),
    task.lock_version = task.lock_version + 1
WHERE task.state = 'PENDING'
  AND task.execution_lane = 'DEVICE'
  AND task.task_category = 'BUSINESS_INTENT';
