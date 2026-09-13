CREATE ALIAS UTC_TIMESTAMP FOR "org.enveloping.ecobin.device.application.target.DevicePolicyIntegrationTest.utcTimestamp";
CREATE ALIAS HEX FOR "org.enveloping.ecobin.device.application.target.DevicePolicyIntegrationTest.hex";
CREATE TABLE iam_platform_admin(id BIGINT PRIMARY KEY, display_name VARCHAR);
CREATE TABLE iam_tenant(id BIGINT PRIMARY KEY, status VARCHAR);
CREATE TABLE iam_organization(id BIGINT PRIMARY KEY, tenant_id BIGINT, status VARCHAR);
CREATE TABLE iam_staff_account(id BIGINT PRIMARY KEY, tenant_id BIGINT, display_name VARCHAR);
INSERT INTO iam_platform_admin VALUES (1, '测试管理员');
INSERT INTO iam_tenant VALUES (7, 'ENABLED');
INSERT INTO iam_organization VALUES (9, 7, 'ENABLED');
INSERT INTO iam_staff_account VALUES (2, 7, '租户负责人');
CREATE TABLE dev_runtime_snapshot_policy(singleton_id INT, policy_version BIGINT, fallback_interval_ms BIGINT,
    rollout_uid VARCHAR, rollout_status VARCHAR, next_asset_id BIGINT, target_asset_count BIGINT,
    processed_asset_count BIGINT, published_asset_count BIGINT, publication_source VARCHAR,
    updated_by_platform_admin_id BIGINT, change_reason VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP,
    updated_at TIMESTAMP, lock_version BIGINT);
INSERT INTO dev_runtime_snapshot_policy VALUES(1, 1, 3600000, '00000000-0000-4000-8000-000000000046', 'DONE', 0, 0, 0, 0,
    'SYSTEM', NULL, '初始设置', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0);
CREATE TABLE dev_device_default_policy(singleton_id INT PRIMARY KEY, policy_version BIGINT, fullness_mode VARCHAR,
    fullness_weight_kg DECIMAL(10,3), rollout_uid VARCHAR, rollout_status VARCHAR, next_asset_id BIGINT,
    target_asset_count BIGINT, processed_asset_count BIGINT, published_asset_count BIGINT, publication_source VARCHAR,
    updated_by_platform_admin_id BIGINT, change_reason VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP,
    updated_at TIMESTAMP, lock_version BIGINT);
INSERT INTO dev_device_default_policy VALUES(1, 1, 'INFRARED_OR_WEIGHT', 50.000, '00000000-0000-4000-8000-000000000070', 'PENDING', 0, 0, 0, 0,
    'SYSTEM', NULL, '统一默认设置', CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, 0);
ALTER TABLE dev_device_default_policy ADD unit_price_yuan_per_kg DECIMAL(15,4) DEFAULT 0.4500;
ALTER TABLE dev_device_default_policy ADD negative_weight_threshold_g BIGINT DEFAULT 500;
CREATE TABLE dev_tenant_device_policy(tenant_id BIGINT PRIMARY KEY, policy_version BIGINT,
 configuration_mode VARCHAR, unit_price_yuan_per_kg DECIMAL(15,4), fullness_mode VARCHAR, fullness_weight_kg DECIMAL(10,3), negative_weight_threshold_g BIGINT,
 rollout_uid VARCHAR, rollout_status VARCHAR, next_asset_id BIGINT DEFAULT 0, target_asset_count BIGINT DEFAULT 0,
 processed_asset_count BIGINT DEFAULT 0, published_asset_count BIGINT DEFAULT 0, updated_by_staff_account_id BIGINT,
 change_reason VARCHAR, started_at TIMESTAMP, completed_at TIMESTAMP, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0);
INSERT INTO iam_tenant VALUES (8, 'ENABLED');
INSERT INTO iam_organization VALUES (10, 7, 'ENABLED'), (11, 8, 'ENABLED');
INSERT INTO iam_staff_account VALUES (3, 8, '另一租户负责人');
CREATE TABLE dev_device_asset(id BIGINT PRIMARY KEY, hardware_sn VARCHAR, device_public_code VARCHAR, model_name VARCHAR,
    expected_port_count INT, tenant_id BIGINT, organization_id BIGINT, acceptance_status VARCHAR, lifecycle_status VARCHAR, control_version BIGINT);
CREATE TABLE dev_port(id BIGINT PRIMARY KEY, asset_id BIGINT, tenant_id BIGINT, organization_id BIGINT, port_no INT);
CREATE TABLE dev_config_version(id BIGINT AUTO_INCREMENT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, asset_id BIGINT,
    version_no BIGINT, schema_version INT, edge_heartbeat_interval_ms BIGINT, edge_heartbeat_miss_threshold BIGINT,
    runtime_snapshot_policy_version_no BIGINT, device_default_policy_version_no BIGINT, tenant_device_policy_version_no BIGINT, mcu_heartbeat_interval_ms BIGINT,
    mcu_heartbeat_miss_threshold BIGINT, door_close_retry_limit BIGINT, continue_delivery_wait_ms BIGINT,
    negative_weight_threshold_g BIGINT, delivery_auto_close_ms BIGINT, weight_measurement_timeout_ms BIGINT,
    delivery_door_travel_wait_ms BIGINT, clean_solenoid_pulse_ms BIGINT, smoke_monitoring_enabled BOOLEAN,
    content_sha256 VARBINARY(32), mcu_payload_sha256 VARBINARY(32), publication_source VARCHAR,
    published_by_staff_account_id BIGINT, published_at TIMESTAMP, created_at TIMESTAMP,
    UNIQUE(asset_id, version_no));
CREATE TABLE dev_port_config_snapshot(id BIGINT AUTO_INCREMENT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT,
    asset_id BIGINT, config_version_id BIGINT, port_id BIGINT, display_name VARCHAR, business_enabled BOOLEAN,
    unit_price_yuan_per_kg DECIMAL(12,4), fullness_mode VARCHAR, configured_full_weight_g BIGINT,
    delivery_settle_delay_ms BIGINT, fullness_settle_wait_ms BIGINT, fullness_sensor_kind VARCHAR,
    fullness_distance_threshold_mm BIGINT, fullness_sample_count INT, fullness_min_valid_sample_count INT,
    fullness_echo_timeout_us BIGINT, fullness_confirmation_wait_ms BIGINT, door_auto_close_timeout_ms BIGINT,
    weight_stable_window_ms BIGINT, weight_maximum_fluctuation_g BIGINT, weight_required_sample_count INT,
    weight_measurement_timeout_ms BIGINT, weight_minimum_g BIGINT, weight_maximum_g BIGINT, calibration_version BIGINT,
    infrared_sample_timeout_ms BIGINT, delivery_door_operation_timeout_ms BIGINT, created_at TIMESTAMP);
CREATE TABLE dev_config_application(id BIGINT AUTO_INCREMENT PRIMARY KEY, application_uid VARCHAR, tenant_id BIGINT,
    organization_id BIGINT, asset_id BIGINT, config_version_id BIGINT, status VARCHAR, reported_version_no BIGINT,
    reported_content_sha256 VARBINARY(32), reported_mcu_payload_sha256 VARBINARY(32), edge_persisted_at TIMESTAMP,
    mcu_synced_at TIMESTAMP, applied_at TIMESTAMP, last_failure_at TIMESTAMP, last_failure_code VARCHAR,
    lock_version BIGINT, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE dev_device_command(id BIGINT AUTO_INCREMENT PRIMARY KEY, command_uid VARCHAR, tenant_id BIGINT,
    organization_id BIGINT, asset_id BIGINT, command_type VARCHAR, delivery_session_id BIGINT, clean_operation_id BIGINT,
    config_application_id BIGINT, fullness_detection_id BIGINT, baseline_measurement_id BIGINT, payload_schema_version INT,
    semantic_payload JSON, semantic_payload_sha256 VARBINARY(32), physical_state VARCHAR, queued_at TIMESTAMP,
    edge_accepted_at TIMESTAMP, physical_started_at TIMESTAMP, physical_ended_at TIMESTAMP, lock_version BIGINT,
    created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE ops_reliable_task(task_type VARCHAR, target_type VARCHAR, target_stable_key VARCHAR, state VARCHAR);
