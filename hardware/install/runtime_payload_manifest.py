"""Canonical source inventory and schema declaration for runtime releases.

Keep this module dependency-free: it is imported by both the signed runtime
release tooling and the immutable image builder.
"""

from __future__ import annotations


EDGE_SCHEMA_VERSION = "18"

RUNTIME_APP_FILES = (
    "business_control.py",
    "business_message_handler.py",
    "business_outbox_relay.py",
    "camera_capture.py",
    "cloud_transport.py",
    "command_processor.py",
    "config.py",
    "cos_photo_uploader.py",
    "device_acceptance.py",
    "device_credentials.py",
    "device_entry_url_refresh.py",
    "device_identity.py",
    "direct_onenet_transport.py",
    "edge_boot.py",
    "edge_identity.py",
    "edge_store.py",
    "edge_store_prepare.py",
    "fixed_frame_health_recovery.py",
    "fixed_frame_mcu_adapter.py",
    "factory_progress.py",
    "factory_seal/__init__.py",
    "factory_seal/admission.py",
    "factory_seal/errors.py",
    "factory_seal/runtime.py",
    "factory_seal/validation.py",
    "main.py",
    "job_safety.py",
    "local_control.py",
    "mcu_firmware_package.py",
    "mcu_firmware_updater.py",
    "mqtt_client.py",
    "onenet_projection_model.json",
    "onenet_wire.py",
    "photo_manager.py",
    "remote_support_control.py",
    "simulated_camera.py",
    "system/__init__.py",
    "system/mcu_safe_gpio.py",
    "system/orangepi_boot_config.py",
    "trusted_clock.py",
    "uart_link.py",
    "uart_protocol.py",
    "work_manager.py",
)

# Schema-v1 image payloads predate the local communication/control boundary.
# Auditing an already-built v1 image must keep using that historical exact
# source inventory; adding the stage-three client modules does not silently
# redefine what an old lock meant.
LEGACY_RUNTIME_APP_FILES = (
    "business_message_handler.py",
    "business_outbox_relay.py",
    "camera_capture.py",
    "cloud_transport.py",
    "command_processor.py",
    "config.py",
    "cos_photo_uploader.py",
    "device_acceptance.py",
    "device_credentials.py",
    "device_entry_url_refresh.py",
    "device_identity.py",
    "direct_onenet_transport.py",
    "edge_boot.py",
    "edge_identity.py",
    "edge_store.py",
    "edge_store_prepare.py",
    "fixed_frame_health_recovery.py",
    "fixed_frame_mcu_adapter.py",
    "factory_progress.py",
    "factory_seal/__init__.py",
    "factory_seal/admission.py",
    "factory_seal/errors.py",
    "factory_seal/runtime.py",
    "factory_seal/validation.py",
    "main.py",
    "mcu_firmware_package.py",
    "mcu_firmware_updater.py",
    "mqtt_client.py",
    "onenet_projection_model.json",
    "onenet_wire.py",
    "photo_manager.py",
    "remote_support_control.py",
    "simulated_camera.py",
    "system/__init__.py",
    "system/mcu_safe_gpio.py",
    "system/orangepi_boot_config.py",
    "trusted_clock.py",
    "uart_link.py",
    "uart_protocol.py",
    "work_manager.py",
)

# Permanent device-management components are installed by the controlled
# image, not by a replaceable business-runtime release.  Keep independent
# exact inventories so neither component can accidentally import the other's
# private store implementation.
COMMUNICATION_AGENT_FILES = (
    "communication_agent.py",
    "communication_store.py",
    "local_control.py",
)

DEVICE_UPDATER_FILES = (
    "device_management_preflight.py",
    "local_control.py",
    "updater_agent.py",
    "updater_control_cli.py",
    "updater_store.py",
)

DEVICE_UPDATER_HELPER_FILES = (
    "__init__.py",
    "privileged_control.py",
    "business_activation_helper.py",
    "business_activation_primitives.py",
    "mcu_flash_helper.py",
    "mcu_flash_primitives.py",
    "mcu_flash_recovery.py",
)

DEVICE_UPDATER_HELPER_UNIT_FILES = (
    "ecobin-business-activation-helper.socket",
    "ecobin-business-activation-helper@.service",
    "ecobin-mcu-flash-helper.socket",
    "ecobin-mcu-flash-helper@.service",
)

# The factory/first-boot application is a separate staged application and
# cannot import from /opt/ecobin/hardware/current/app.  These are the exact
# top-level runtime sources needed by its real systemd entry points.
FACTORY_APP_RUNTIME_FILES = (
    "camera_capture.py",
    "device_credentials.py",
    "factory_progress.py",
    "fixed_frame_mcu_adapter.py",
    "onenet_projection_model.json",
    "onenet_wire.py",
    "simulated_camera.py",
    "system/__init__.py",
    "system/mcu_safe_gpio.py",
    "trusted_clock.py",
)
