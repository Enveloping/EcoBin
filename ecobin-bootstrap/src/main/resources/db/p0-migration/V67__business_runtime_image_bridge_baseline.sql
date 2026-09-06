-- V67: preserve the one-time image-bridge baseline as a truthful update fact.
--
-- A newly cut-over device runs the healthy business program embedded in its
-- accepted system image before it has installed its first independent
-- business release.  That source has no release UID or release sequence.  The
-- deployment freezes the baseline kind explicitly instead of inventing a
-- release identity, and later rollback evidence may truthfully contain no
-- installed release while still proving that the database was restored.

ALTER TABLE dev_edge_software_deployment
    DROP CHECK ck_dev_edge_deployment_uid,
    DROP CHECK ck_dev_edge_deployment_state,
    ADD COLUMN source_business_baseline_kind VARCHAR(24)
        CHARACTER SET ascii COLLATE ascii_bin
        NOT NULL DEFAULT 'BUSINESS_RELEASE'
        AFTER source_management_state_sequence,
    MODIFY COLUMN source_business_release_uid CHAR(36)
        CHARACTER SET ascii COLLATE ascii_bin NULL,
    MODIFY COLUMN source_business_release_sequence BIGINT UNSIGNED NULL,
    ADD CONSTRAINT ck_dev_edge_deployment_uid CHECK (
        deployment_uid = LOWER(deployment_uid)
        AND deployment_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        AND (
            source_business_release_uid IS NULL
            OR (
                source_business_release_uid = LOWER(source_business_release_uid)
                AND source_business_release_uid REGEXP
                    '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            )
        )
    ),
    ADD CONSTRAINT ck_dev_edge_deployment_source_baseline CHECK (
        (
            source_business_baseline_kind = 'BUSINESS_RELEASE'
            AND source_business_release_uid IS NOT NULL
            AND source_business_release_sequence
                BETWEEN 1 AND 9007199254740991
        )
        OR (
            source_business_baseline_kind = 'IMAGE_BRIDGE'
            AND source_business_release_uid IS NULL
            AND source_business_release_sequence IS NULL
        )
    ),
    ADD CONSTRAINT ck_dev_edge_deployment_state CHECK (
        deployment_status IN (
            'PLANNED', 'QUEUED', 'RECEIVED', 'DOWNLOADING',
            'VERIFYING_PACKAGE', 'PACKAGE_READY', 'WAITING_FOR_IDLE',
            'MIGRATING_DATA', 'ACTIVATING', 'VERIFYING_TARGET',
            'OBSERVING', 'ROLLING_BACK', 'VERIFYING_ROLLBACK',
            'SUCCEEDED', 'ROLLED_BACK', 'DEFERRED', 'REJECTED',
            'FAILED_LOCKED', 'DOWNLOAD_AUTHORIZATION_REQUIRED',
            'CANCELLED'
        )
        AND eligibility_status = 'ELIGIBLE'
        AND source_management_state_sequence BETWEEN 1 AND 9007199254740991
        AND stage_sequence BETWEEN 0 AND 9007199254740991
        AND business_admission_state IN (
            'OPEN', 'DRAINING', 'MAINTENANCE', 'LOCKED'
        )
        AND download_attempt_count BETWEEN 0 AND 10
        AND target_attempt_count BETWEEN 0 AND 10
        AND rollback_attempt_count BETWEEN 0 AND 10
        AND database_restored IN (0, 1)
        AND lock_version >= 0
        AND (
            (deployment_status = 'PLANNED'
                AND command_uid IS NULL
                AND reliable_task_uid IS NULL
                AND control_sequence IS NULL
                AND queued_at IS NULL)
            OR (deployment_status <> 'PLANNED'
                AND command_uid IS NOT NULL
                AND reliable_task_uid IS NOT NULL
                AND control_sequence BETWEEN 1 AND 9007199254740991
                AND queued_at IS NOT NULL)
        )
        AND cancellation_status IN (
            'NONE', 'QUEUED', 'CANCELLED', 'TOO_LATE'
        )
        AND (
            cancellation_status = 'NONE'
            OR cancel_control_sequence > control_sequence
        )
        AND (
            (cancellation_status = 'NONE'
                AND cancel_command_uid IS NULL
                AND cancel_reliable_task_uid IS NULL
                AND cancel_control_sequence IS NULL
                AND cancel_reason IS NULL
                AND cancel_requested_by_platform_admin_id IS NULL
                AND cancel_requested_at IS NULL
                AND cancel_result_at IS NULL)
            OR (cancellation_status = 'QUEUED'
                AND cancel_command_uid IS NOT NULL
                AND cancel_reliable_task_uid IS NOT NULL
                AND cancel_control_sequence BETWEEN 1 AND 9007199254740991
                AND CHAR_LENGTH(TRIM(cancel_reason)) BETWEEN 1 AND 500
                AND cancel_requested_by_platform_admin_id IS NOT NULL
                AND cancel_requested_at IS NOT NULL
                AND cancel_result_at IS NULL)
            OR (cancellation_status IN ('CANCELLED', 'TOO_LATE')
                AND cancel_command_uid IS NOT NULL
                AND cancel_reliable_task_uid IS NOT NULL
                AND cancel_control_sequence BETWEEN 1 AND 9007199254740991
                AND CHAR_LENGTH(TRIM(cancel_reason)) BETWEEN 1 AND 500
                AND cancel_requested_by_platform_admin_id IS NOT NULL
                AND cancel_requested_at IS NOT NULL
                AND cancel_result_at IS NOT NULL)
        )
        AND (
            (deployment_status = 'CANCELLED'
                AND cancellation_status = 'CANCELLED')
            OR (deployment_status <> 'CANCELLED'
                AND cancellation_status <> 'CANCELLED')
        )
    );
