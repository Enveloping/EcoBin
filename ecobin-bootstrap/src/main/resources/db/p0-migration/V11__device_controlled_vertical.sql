-- Complete the device configuration snapshot required by the generated
-- OneNet/UART machine contracts and admit every registered edge-event header.

ALTER TABLE dev_config_version
    ADD COLUMN delivery_door_travel_wait_ms BIGINT NOT NULL
        DEFAULT 30000
        AFTER weight_measurement_timeout_ms,
    ADD CONSTRAINT ck_dev_config_door_travel_wait CHECK (
        delivery_door_travel_wait_ms BETWEEN 30000 AND 45000
    );

ALTER TABLE dev_port_config_snapshot
    ADD COLUMN fullness_sensor_kind VARCHAR(24) CHARACTER SET ascii
        COLLATE ascii_bin NOT NULL DEFAULT 'ULTRASONIC'
        AFTER fullness_settle_wait_ms,
    ADD COLUMN fullness_distance_threshold_mm BIGINT NOT NULL DEFAULT 600
        AFTER fullness_sensor_kind,
    ADD COLUMN fullness_sample_count INT NOT NULL DEFAULT 5
        AFTER fullness_distance_threshold_mm,
    ADD COLUMN fullness_min_valid_sample_count INT NOT NULL DEFAULT 3
        AFTER fullness_sample_count,
    ADD COLUMN fullness_echo_timeout_us BIGINT NOT NULL DEFAULT 30000
        AFTER fullness_min_valid_sample_count,
    ADD CONSTRAINT ck_dev_port_config_sensor_kind CHECK (
        fullness_sensor_kind IN ('ULTRASONIC', 'DIGITAL_INFRARED')
    ),
    ADD CONSTRAINT ck_dev_port_config_sensor_parameters CHECK (
        fullness_distance_threshold_mm BETWEEN 1 AND 4000
        AND fullness_sample_count BETWEEN 3 AND 9
        AND fullness_min_valid_sample_count BETWEEN 1
            AND fullness_sample_count
        AND fullness_echo_timeout_us BETWEEN 100 AND 100000
    );

ALTER TABLE dev_edge_event
    DROP CHECK ck_dev_edge_event_type_class,
    DROP CHECK ck_dev_edge_event_target_pair,
    ADD CONSTRAINT ck_dev_edge_event_type_class CHECK (
        (
            event_type IN (
                'DEVICE_COMMAND_OBSERVED',
                'CONFIGURATION_PROGRESS',
                'DELIVERY_COMPLETE',
                'CLEAN_COMPLETE',
                'FULLNESS_SAMPLE_COMPLETE',
                'BASELINE_MEASUREMENT_COMPLETE',
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND delivery_class = 'RELIABLE_FACT'
        )
        OR
        (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND delivery_class = 'CONTROL_RECEIPT'
        )
        OR
        (
            event_type = 'DEVICE_RUNTIME_SNAPSHOT'
            AND delivery_class = 'TELEMETRY_SNAPSHOT'
        )
    ),
    ADD CONSTRAINT ck_dev_edge_event_target_pair CHECK (
        (
            event_type = 'DEVICE_COMMAND_OBSERVED'
            AND target_type = 'DEVICE_COMMAND'
        )
        OR
        (
            event_type = 'CONFIGURATION_PROGRESS'
            AND target_type = 'CONFIGURATION_APPLICATION'
        )
        OR
        (
            event_type = 'DELIVERY_COMPLETE'
            AND target_type = 'DELIVERY_SESSION'
        )
        OR
        (
            event_type = 'CLEAN_COMPLETE'
            AND target_type = 'CLEAN_OPERATION'
        )
        OR
        (
            event_type = 'FULLNESS_SAMPLE_COMPLETE'
            AND target_type = 'FULLNESS_DETECTION'
        )
        OR
        (
            event_type = 'BASELINE_MEASUREMENT_COMPLETE'
            AND target_type = 'BASELINE_MEASUREMENT'
        )
        OR
        (
            event_type IN (
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'DEVICE_RUNTIME_SNAPSHOT'
            )
            AND target_type = 'DEVICE_DEPLOYMENT'
        )
        OR
        (
            event_type IN (
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND target_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        )
        OR
        (
            event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND target_type = 'BUSINESS_CONFIRMATION'
        )
    );
