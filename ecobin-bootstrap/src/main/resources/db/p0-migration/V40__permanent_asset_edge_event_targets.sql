-- V40 finishes the V36 permanent-device cutover for edge-event targets.
-- Runtime, fault and safety events now identify the permanent asset directly;
-- the removed deployment model must not remain in either new or historical rows.

ALTER TABLE dev_edge_event
    DROP CHECK ck_dev_edge_event_target_pair,
    DROP CHECK ck_dev_edge_event_target_type;

UPDATE dev_edge_event
SET target_type = 'DEVICE_ASSET'
WHERE target_type = 'DEVICE_DEPLOYMENT';

ALTER TABLE dev_edge_event
    ADD CONSTRAINT ck_dev_edge_event_target_type CHECK (
        target_type IN (
            'DEVICE_COMMAND',
            'CONFIGURATION_APPLICATION',
            'DELIVERY_SESSION',
            'CLEAN_OPERATION',
            'FULLNESS_DETECTION',
            'PORT_FULLNESS_STATE',
            'BASELINE_MEASUREMENT',
            'DEVICE_ASSET',
            'BUSINESS_CONFIRMATION',
            'PHOTO_GRANT_REQUEST'
        )
    ),
    ADD CONSTRAINT ck_dev_edge_event_target_pair CHECK (
        (event_type = 'DEVICE_COMMAND_OBSERVED'
            AND target_type = 'DEVICE_COMMAND')
        OR (event_type = 'CONFIGURATION_PROGRESS'
            AND target_type = 'CONFIGURATION_APPLICATION')
        OR (event_type = 'DELIVERY_COMPLETE'
            AND target_type = 'DELIVERY_SESSION')
        OR (event_type = 'CLEAN_COMPLETE'
            AND target_type = 'CLEAN_OPERATION')
        OR (event_type = 'FULLNESS_SAMPLE_COMPLETE'
            AND target_type = 'FULLNESS_DETECTION')
        OR (event_type = 'FULLNESS_STATE_CHANGED'
            AND target_type = 'PORT_FULLNESS_STATE')
        OR (event_type = 'BASELINE_MEASUREMENT_COMPLETE'
            AND target_type = 'BASELINE_MEASUREMENT')
        OR (
            event_type IN (
                'DEVICE_FAULT_OBSERVED',
                'DEVICE_FAULT_RECOVERED',
                'SAFETY_SENSOR_STATE_CHANGED',
                'DEVICE_RUNTIME_SNAPSHOT'
            )
            AND target_type = 'DEVICE_ASSET'
        )
        OR (
            event_type IN (
                'PHOTO_STATUS_REPORTED',
                'PHOTO_UPLOAD_GRANT_REQUESTED'
            )
            AND target_type IN ('DELIVERY_SESSION', 'CLEAN_OPERATION')
        )
        OR (event_type = 'BUSINESS_CONFIRMATION_RECEIPT'
            AND target_type = 'BUSINESS_CONFIRMATION')
    );
