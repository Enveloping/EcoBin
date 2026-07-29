-- Align the delivery persistence model with the generated OneNet contract.
-- The backend persists facts reported by the trusted Orange Pi. It must not
-- turn a dispatched close command into a fabricated CLOSED/OK observation.

-- A bag needs a stable public identity for OneNet commands. Existing bag
-- codes remain the operator-facing identity, while bag_uid is derived once
-- for pre-V14 rows and then frozen by NOT NULL/UNIQUE constraints.
ALTER TABLE rec_bag
    ADD COLUMN bag_uid
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER id;

UPDATE rec_bag
SET bag_uid = LOWER(
    CONCAT(
        SUBSTRING(
            SHA2(
                CONCAT(
                    'ecobin:bag:v1:',
                    tenant_id,
                    ':',
                    organization_id,
                    ':',
                    id,
                    ':',
                    HEX(bag_code)
                ),
                256
            ),
            1,
            8
        ),
        '-',
        SUBSTRING(
            SHA2(
                CONCAT(
                    'ecobin:bag:v1:',
                    tenant_id,
                    ':',
                    organization_id,
                    ':',
                    id,
                    ':',
                    HEX(bag_code)
                ),
                256
            ),
            9,
            4
        ),
        '-4',
        SUBSTRING(
            SHA2(
                CONCAT(
                    'ecobin:bag:v1:',
                    tenant_id,
                    ':',
                    organization_id,
                    ':',
                    id,
                    ':',
                    HEX(bag_code)
                ),
                256
            ),
            14,
            3
        ),
        '-',
        ELT(
            MOD(
                CONV(
                    SUBSTRING(
                        SHA2(
                            CONCAT(
                                'ecobin:bag:v1:',
                                tenant_id,
                                ':',
                                organization_id,
                                ':',
                                id,
                                ':',
                                HEX(bag_code)
                            ),
                            256
                        ),
                        17,
                        1
                    ),
                    16,
                    10
                ),
                4
            ) + 1,
            '8',
            '9',
            'a',
            'b'
        ),
        SUBSTRING(
            SHA2(
                CONCAT(
                    'ecobin:bag:v1:',
                    tenant_id,
                    ':',
                    organization_id,
                    ':',
                    id,
                    ':',
                    HEX(bag_code)
                ),
                256
            ),
            18,
            3
        ),
        '-',
        SUBSTRING(
            SHA2(
                CONCAT(
                    'ecobin:bag:v1:',
                    tenant_id,
                    ':',
                    organization_id,
                    ':',
                    id,
                    ':',
                    HEX(bag_code)
                ),
                256
            ),
            21,
            12
        )
    )
)
WHERE bag_uid IS NULL;

ALTER TABLE rec_bag
    MODIFY COLUMN bag_uid
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT uq_rec_bag_uid UNIQUE (bag_uid),
    ADD CONSTRAINT uq_rec_bag_session_uid_identity UNIQUE (
        tenant_id,
        organization_id,
        id,
        bag_uid
    ),
    ADD CONSTRAINT ck_rec_bag_uid_v4 CHECK (
        bag_uid REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    );

-- Freeze the public bag identity together with the existing bag id/code
-- snapshot. The old id/code foreign key remains as an independent proof.
ALTER TABLE dev_delivery_session
    ADD COLUMN bag_uid_snapshot
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER bag_id;

UPDATE dev_delivery_session AS delivery_session
JOIN rec_bag AS bag
    ON bag.tenant_id = delivery_session.tenant_id
    AND bag.organization_id = delivery_session.organization_id
    AND bag.id = delivery_session.bag_id
    AND bag.bag_code = delivery_session.bag_code_snapshot
SET delivery_session.bag_uid_snapshot = bag.bag_uid
WHERE delivery_session.bag_uid_snapshot IS NULL;

ALTER TABLE dev_delivery_session
    MODIFY COLUMN bag_uid_snapshot
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_dev_delivery_session_bag_uid CHECK (
        bag_uid_snapshot REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT fk_dev_delivery_session_bag_uid
        FOREIGN KEY (
            tenant_id,
            organization_id,
            bag_id,
            bag_uid_snapshot
        )
        REFERENCES rec_bag (
            tenant_id,
            organization_id,
            id,
            bag_uid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT uq_dev_delivery_session_order_uid_ref UNIQUE (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        id,
        organization_user_id,
        delivery_config_version_id,
        delivery_config_content_sha256,
        unit_price_yuan_per_kg,
        open_balance_floor_cent,
        max_review_abs_weight_g,
        negative_weight_anomaly_threshold_g,
        bag_id,
        bag_uid_snapshot,
        bag_code_snapshot
    ),
    ADD INDEX ix_dev_delivery_session_bag_uid_fk (
        tenant_id,
        organization_id,
        bag_id,
        bag_uid_snapshot
    );

-- Orders copy the same immutable public bag identity. Keeping the original
-- session snapshot FK and adding this stronger FK permits an incremental
-- migration without weakening any V5/V8 relationship.
ALTER TABLE rec_delivery_order
    ADD COLUMN bag_uid_snapshot
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER bag_id;

UPDATE rec_delivery_order AS delivery_order
JOIN dev_delivery_session AS delivery_session
    ON delivery_session.tenant_id = delivery_order.tenant_id
    AND delivery_session.organization_id = delivery_order.organization_id
    AND delivery_session.deployment_id = delivery_order.deployment_id
    AND delivery_session.port_id = delivery_order.port_id
    AND delivery_session.id = delivery_order.delivery_session_id
SET delivery_order.bag_uid_snapshot =
    delivery_session.bag_uid_snapshot
WHERE delivery_order.bag_uid_snapshot IS NULL;

ALTER TABLE rec_delivery_order
    MODIFY COLUMN bag_uid_snapshot
        CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ADD CONSTRAINT ck_rec_delivery_order_bag_uid CHECK (
        bag_uid_snapshot REGEXP
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
    ),
    ADD CONSTRAINT fk_rec_delivery_order_bag_uid
        FOREIGN KEY (
            tenant_id,
            organization_id,
            bag_id,
            bag_uid_snapshot
        )
        REFERENCES rec_bag (
            tenant_id,
            organization_id,
            id,
            bag_uid
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD CONSTRAINT fk_rec_delivery_order_session_uid_snapshot
        FOREIGN KEY (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            delivery_session_id,
            organization_user_id,
            delivery_config_version_id,
            delivery_config_content_sha256,
            unit_price_yuan_per_kg,
            open_balance_floor_cent,
            max_review_abs_weight_g,
            negative_weight_anomaly_threshold_g,
            bag_id,
            bag_uid_snapshot,
            bag_code_snapshot
        )
        REFERENCES dev_delivery_session (
            tenant_id,
            organization_id,
            deployment_id,
            port_id,
            id,
            organization_user_id,
            delivery_config_version_id,
            delivery_config_content_sha256,
            unit_price_yuan_per_kg,
            open_balance_floor_cent,
            max_review_abs_weight_g,
            negative_weight_anomaly_threshold_g,
            bag_id,
            bag_uid_snapshot,
            bag_code_snapshot
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_rec_delivery_order_bag_uid_fk (
        tenant_id,
        organization_id,
        bag_id,
        bag_uid_snapshot
    ),
    ADD INDEX ix_rec_delivery_order_session_uid_snapshot_fk (
        tenant_id,
        organization_id,
        deployment_id,
        port_id,
        delivery_session_id,
        organization_user_id,
        delivery_config_version_id,
        delivery_config_content_sha256,
        unit_price_yuan_per_kg,
        open_balance_floor_cent,
        max_review_abs_weight_g,
        negative_weight_anomaly_threshold_g,
        bag_id,
        bag_uid_snapshot,
        bag_code_snapshot
    );

-- Preserve all delivery-complete measurement and command facts carried by the
-- generated schema. Legacy CLOSED/OK columns remain for binary compatibility
-- but V14 requires them to be NULL for every newly valid delivery fact.
ALTER TABLE dev_physical_result
    ADD COLUMN delivery_pre_weight_value_available TINYINT NULL
        AFTER delivery_pre_last_observed_weight_g,
    ADD COLUMN delivery_pre_weight_value_kind
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_pre_weight_value_available,
    ADD COLUMN delivery_post_weight_value_available TINYINT NULL
        AFTER delivery_post_last_observed_weight_g,
    ADD COLUMN delivery_post_weight_value_kind
        VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_post_weight_value_available,
    ADD COLUMN delivery_manual_review_required TINYINT NULL
        AFTER delivery_completion_reason,
    ADD COLUMN delivery_final_door_command
        VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_final_door_health,
    ADD COLUMN delivery_final_door_output_status
        VARCHAR(48) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_final_door_command,
    ADD COLUMN delivery_final_door_physical_state_basis
        VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NULL
        AFTER delivery_final_door_output_status,
    DROP CHECK ck_dev_result_branch_fields,
    DROP CHECK ck_dev_result_delivery_shape;

-- V13 rows predate the explicit contract fields. Keep their known weights,
-- downgrade the old synthetic door observation to NOT_OBSERVABLE, and use a
-- neutral command fact rather than claiming a dispatch that was not stored.
UPDATE dev_physical_result
SET delivery_pre_weight_value_available = 1,
    delivery_pre_weight_value_kind = 'STABLE_WINDOW_MEAN',
    delivery_post_weight_value_available =
        CASE
            WHEN delivery_post_weight_g IS NOT NULL
                OR delivery_post_last_observed_weight_g IS NOT NULL
            THEN 1
            ELSE 0
        END,
    delivery_post_weight_value_kind =
        CASE
            WHEN delivery_post_measurement_status = 'STABLE'
            THEN 'STABLE_WINDOW_MEAN'
            WHEN delivery_post_last_observed_weight_g IS NOT NULL
            THEN 'LAST_OBSERVED'
            ELSE 'NONE'
        END,
    delivery_manual_review_required = 0,
    delivery_final_door_command = 'NONE',
    delivery_final_door_output_status = 'NOT_DISPATCHED',
    delivery_final_door_physical_state_basis = 'NOT_OBSERVABLE',
    delivery_final_door_state = NULL,
    delivery_final_door_health = NULL
WHERE result_type = 'DELIVERY';

ALTER TABLE dev_physical_result
    ADD CONSTRAINT ck_dev_result_delivery_pre_weight_value CHECK (
        (
            delivery_pre_measurement_uid IS NULL
            AND delivery_pre_weight_value_available IS NULL
            AND delivery_pre_weight_value_kind IS NULL
        )
        OR
        (
            delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_weight_value_available IN (0, 1)
            AND delivery_pre_weight_value_kind IN (
                'NONE',
                'STABLE_WINDOW_MEAN',
                'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN',
                'LAST_OBSERVED'
            )
            AND (
                (
                    delivery_pre_weight_value_available = 0
                    AND delivery_pre_weight_value_kind = 'NONE'
                    AND delivery_pre_weight_g IS NULL
                    AND delivery_pre_last_observed_weight_g IS NULL
                )
                OR
                (
                    delivery_pre_weight_value_available = 1
                    AND delivery_pre_weight_value_kind <> 'NONE'
                    AND (
                        (
                            delivery_pre_measurement_status = 'STABLE'
                            AND delivery_pre_weight_value_kind =
                                'STABLE_WINDOW_MEAN'
                            AND delivery_pre_weight_g IS NOT NULL
                        )
                        OR
                        (
                            delivery_pre_measurement_status <> 'STABLE'
                            AND delivery_pre_last_observed_weight_g IS NOT NULL
                        )
                    )
                )
            )
        )
    ),
    ADD CONSTRAINT ck_dev_result_delivery_post_weight_value CHECK (
        (
            delivery_post_measurement_uid IS NULL
            AND delivery_post_weight_value_available IS NULL
            AND delivery_post_weight_value_kind IS NULL
        )
        OR
        (
            delivery_post_measurement_uid IS NOT NULL
            AND delivery_post_weight_value_available IN (0, 1)
            AND delivery_post_weight_value_kind IN (
                'NONE',
                'STABLE_WINDOW_MEAN',
                'LAST_FOUR_MEAN',
                'AVAILABLE_SAMPLES_MEAN',
                'LAST_OBSERVED'
            )
            AND (
                (
                    delivery_post_weight_value_available = 0
                    AND delivery_post_weight_value_kind = 'NONE'
                    AND delivery_post_weight_g IS NULL
                    AND delivery_post_last_observed_weight_g IS NULL
                )
                OR
                (
                    delivery_post_weight_value_available = 1
                    AND delivery_post_weight_value_kind <> 'NONE'
                    AND (
                        (
                            delivery_post_measurement_status = 'STABLE'
                            AND delivery_post_weight_value_kind =
                                'STABLE_WINDOW_MEAN'
                            AND delivery_post_weight_g IS NOT NULL
                        )
                        OR
                        (
                            delivery_post_measurement_status <> 'STABLE'
                            AND delivery_post_last_observed_weight_g IS NOT NULL
                        )
                    )
                )
            )
        )
    ),
    ADD CONSTRAINT ck_dev_result_branch_fields CHECK (
        (
            result_type = 'DELIVERY'
            AND delivery_pre_measurement_uid IS NOT NULL
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_manual_review_required IS NOT NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_final_door_command IS NOT NULL
            AND delivery_final_door_output_status IS NOT NULL
            AND delivery_final_door_physical_state_basis IS NOT NULL
            AND delivery_final_door_command IN ('NONE', 'OPEN', 'CLOSE')
            AND delivery_final_door_output_status IN (
                'NOT_DISPATCHED',
                'COMMAND_DISPATCHED',
                'COMMAND_SUPERSEDED_BEFORE_DISPATCH',
                'COALESCED_WITH_EXISTING_CLOSE',
                'OUTPUT_REJECTED'
            )
            AND delivery_final_door_physical_state_basis =
                'NOT_OBSERVABLE'
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
        )
        OR
        (
            result_type = 'CLEAN'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_final_door_command IS NULL
            AND delivery_final_door_output_status IS NULL
            AND delivery_final_door_physical_state_basis IS NULL
            AND delivery_completion_reason IS NULL
            AND delivery_manual_review_required IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NOT NULL
            AND clean_final_measurement_uid IS NOT NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
        )
        OR
        (
            result_type = 'FULLNESS_SAMPLE'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_final_door_command IS NULL
            AND delivery_final_door_output_status IS NULL
            AND delivery_final_door_physical_state_basis IS NULL
            AND delivery_completion_reason IS NULL
            AND delivery_manual_review_required IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NOT NULL
            AND baseline_measurement_uid IS NULL
            AND empty_bag_confirmed IS NULL
        )
        OR
        (
            result_type = 'BASELINE_MEASUREMENT'
            AND delivery_pre_measurement_uid IS NULL
            AND delivery_post_measurement_uid IS NULL
            AND delivery_net_weight_g IS NULL
            AND delivery_final_door_state IS NULL
            AND delivery_final_door_health IS NULL
            AND delivery_final_door_command IS NULL
            AND delivery_final_door_output_status IS NULL
            AND delivery_final_door_physical_state_basis IS NULL
            AND delivery_completion_reason IS NULL
            AND delivery_manual_review_required IS NULL
            AND negative_weight_anomaly IS NULL
            AND clean_pre_measurement_uid IS NULL
            AND clean_final_measurement_uid IS NULL
            AND clean_removed_net_weight_g IS NULL
            AND clean_new_baseline_weight_g IS NULL
            AND cleaner_completion_confirmed IS NULL
            AND clean_lock_power_state IS NULL
            AND clean_solenoid_health IS NULL
            AND clean_door_inferred_state IS NULL
            AND clean_door_state_basis IS NULL
            AND fullness_measurement_uid IS NULL
            AND infrared_value IS NULL
            AND infrared_health IS NULL
            AND baseline_measurement_uid IS NOT NULL
        )
    ),
    ADD CONSTRAINT ck_dev_result_delivery_shape CHECK (
        result_type <> 'DELIVERY'
        OR
        (
            delivery_pre_measurement_uid IS NOT NULL
            AND delivery_pre_measurement_status = 'STABLE'
            AND delivery_pre_weight_g IS NOT NULL
            AND delivery_pre_weight_value_available = 1
            AND delivery_pre_weight_value_kind =
                'STABLE_WINDOW_MEAN'
            AND delivery_post_measurement_uid IS NOT NULL
            AND delivery_completion_reason IS NOT NULL
            AND delivery_manual_review_required = 0
            AND negative_weight_anomaly IN (0, 1)
            AND (
                (
                    delivery_completion_reason IN (
                        'USER_ENDED',
                        'SELECTION_WINDOW_EXPIRED'
                    )
                    AND delivery_post_measurement_status = 'STABLE'
                    AND delivery_post_weight_g IS NOT NULL
                    AND delivery_post_weight_value_available = 1
                    AND delivery_post_weight_value_kind =
                        'STABLE_WINDOW_MEAN'
                    AND delivery_net_weight_g IS NOT NULL
                )
                OR
                (
                    delivery_completion_reason =
                        'TERMINAL_WEIGHT_FAILURE'
                    AND delivery_post_measurement_status IN (
                        'UNSTABLE',
                        'TIMEOUT',
                        'SENSOR_FAULT',
                        'OVERLOAD'
                    )
                    AND delivery_post_weight_g IS NULL
                    AND delivery_net_weight_g IS NULL
                )
            )
        )
    );

-- Completion events can legitimately create pending photo slots. Preserve a
-- safe reason for old pending rows, then accept both contract forms:
-- captured-with-metadata and not-yet-captured. The metadata-free alternatives
-- also preserve any pre-V14 reserved photo UID instead of destroying it.
ALTER TABLE rec_delivery_photo
    DROP CHECK ck_rec_delivery_photo_state;

UPDATE rec_delivery_photo
SET missing_reason = 'UPLOAD_PENDING'
WHERE status = 'UPLOAD_PENDING'
  AND missing_reason IS NULL;

ALTER TABLE rec_delivery_photo
    ADD CONSTRAINT ck_rec_delivery_photo_state CHECK (
        (
            status = 'UPLOAD_PENDING'
            AND object_url IS NULL
            AND linked_at IS NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP
                '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    sha256 IS NULL
                    AND size_bytes IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                )
            )
        )
        OR
        (
            status = 'AVAILABLE'
            AND photo_uid IS NOT NULL
            AND object_url IS NOT NULL
            AND sha256 IS NOT NULL
            AND size_bytes > 0
            AND linked_at IS NOT NULL
            AND missing_reason IS NULL
        )
        OR
        (
            status = 'PERMANENTLY_MISSING'
            AND object_url IS NULL
            AND linked_at IS NOT NULL
            AND missing_reason IS NOT NULL
            AND missing_reason REGEXP
                '^[A-Z][A-Z0-9_]{0,63}$'
            AND (
                (
                    sha256 IS NULL
                    AND size_bytes IS NULL
                )
                OR
                (
                    photo_uid IS NOT NULL
                    AND sha256 IS NOT NULL
                    AND size_bytes > 0
                )
            )
        )
    );

-- A normal miniapp user is an authenticated business actor, not a system or
-- unauthenticated surrogate. Add the missing typed actor branch and channel.
ALTER TABLE ops_audit_log
    ADD COLUMN organization_user_id BIGINT NULL
        AFTER staff_account_id,
    DROP CHECK ck_ops_audit_actor,
    DROP CHECK ck_ops_audit_channel,
    ADD CONSTRAINT ck_ops_audit_actor CHECK (
        (
            actor_kind = 'PLATFORM_ADMIN'
            AND platform_admin_id IS NOT NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
        OR
        (
            actor_kind = 'STAFF_ACCOUNT'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NOT NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
        OR
        (
            actor_kind = 'ORGANIZATION_USER'
            AND scope_kind = 'ORGANIZATION'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NOT NULL
            AND system_actor_code IS NULL
        )
        OR
        (
            actor_kind = 'SYSTEM'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NOT NULL
            AND system_actor_code =
                UPPER(TRIM(system_actor_code))
        )
        OR
        (
            actor_kind = 'UNAUTHENTICATED'
            AND platform_admin_id IS NULL
            AND staff_account_id IS NULL
            AND organization_user_id IS NULL
            AND system_actor_code IS NULL
        )
    ),
    ADD CONSTRAINT ck_ops_audit_channel CHECK (
        entry_channel IN (
            'WEB',
            'MINIAPP_MANAGEMENT',
            'MINIAPP_USER',
            'SYSTEM_TASK',
            'SECURITY_ENTRY'
        )
    ),
    ADD CONSTRAINT fk_ops_audit_organization_user
        FOREIGN KEY (
            tenant_id,
            organization_id,
            organization_user_id
        )
        REFERENCES iam_organization_user (
            tenant_id,
            organization_id,
            id
        )
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    ADD INDEX ix_ops_audit_organization_user_fk (
        tenant_id,
        organization_id,
        organization_user_id
    );

-- Detection happens before persistence. Correct the old reversed comparison;
-- normalize only legacy rows that were admitted solely by the old CHECK.
ALTER TABLE rec_delivery_anomaly
    DROP CHECK ck_rec_delivery_anomaly_times;

UPDATE rec_delivery_anomaly
SET created_at = detected_at
WHERE created_at < detected_at;

ALTER TABLE rec_delivery_anomaly
    ADD CONSTRAINT ck_rec_delivery_anomaly_times
        CHECK (created_at >= detected_at);

-- A trusted Orange Pi can complete a delivery while its wall clock is not
-- synchronized. Keep that absence explicit instead of substituting backend
-- time as a fabricated device occurrence time. Stable visibility ordering is
-- already owned by visibility_sequence_no.
ALTER TABLE rec_delivery_order
    DROP CHECK ck_rec_delivery_order_times,
    MODIFY COLUMN device_occurred_at DATETIME(3) NULL,
    ADD CONSTRAINT ck_rec_delivery_order_times CHECK (
        (
            device_occurred_at IS NULL
            OR backend_received_at >= device_occurred_at
        )
        AND created_at >= backend_received_at
        AND updated_at >= created_at
        AND (
            first_approved_at IS NULL
            OR first_approved_at >= created_at
        )
    );
