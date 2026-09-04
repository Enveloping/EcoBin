"""Canonical source inventory and schema declaration for runtime releases.

Keep this module dependency-free: it is imported by both the signed runtime
release tooling and the immutable image builder.
"""

from __future__ import annotations


EDGE_SCHEMA_VERSION = "18"

RUNTIME_APP_FILES = (
    "business_identity.py",
    "business_control.py",
    "business_message_handler.py",
    "business_outbox_relay.py",
    "business_runtime_cutover_state.py",
    "camera_capture.py",
    "camera_selection.py",
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
    "fixed_frame_mcu_maintenance.py",
    "factory_progress.py",
    "factory_seal/__init__.py",
    "factory_seal/admission.py",
    "factory_seal/errors.py",
    "factory_seal/runtime.py",
    "factory_seal/validation.py",
    "main.py",
    "job_safety.py",
    "local_control.py",
    "local_proxy_cloud_transport.py",
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

# Replaceable business-runtime packages are created only after OneNet
# ownership and MCU-update orchestration have moved into the permanent device
# management layer.  Keep their inventory explicit rather than deriving it at
# build time: a review must make any future boundary change visible.  The
# Omitted modules either open the OneNet device connection/read its key,
# implement the legacy business-owned MCU updater, or provide a root-only GPIO
# helper that is installed by the immutable image instead.
BUSINESS_APP_FILES = tuple(
    name
    for name in RUNTIME_APP_FILES
    if name
    not in {
        "direct_onenet_transport.py",
        "business_runtime_cutover_state.py",
        "device_acceptance.py",
        "device_credentials.py",
        "factory_progress.py",
        "factory_seal/runtime.py",
        "mqtt_client.py",
        "mcu_firmware_package.py",
        "mcu_firmware_updater.py",
        "system/mcu_safe_gpio.py",
        "system/orangepi_boot_config.py",
    }
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
    "cloud_transport.py",
    "communication_agent.py",
    "communication_credentials.py",
    "communication_router.py",
    "communication_store.py",
    "direct_onenet_transport.py",
    "local_control.py",
    "onenet_projection_model.json",
    "onenet_wire.py",
    "trusted_clock.py",
)

DEVICE_UPDATER_FILES = (
    "business_runtime_cutover.py",
    "business_runtime_cutover_state.py",
    "business_update_coordinator.py",
    "business_update_package.py",
    "business_update_store.py",
    "device_management_preflight.py",
    "install/__init__.py",
    "install/business_release.py",
    "install/runtime_payload_manifest.py",
    "install/runtime_release.py",
    "local_control.py",
    "mcu_firmware_package.py",
    "mcu_update_coordinator.py",
    "mcu_update_package.py",
    "mcu_update_store.py",
    "updater_agent.py",
    "updater_control_cli.py",
    "updater_store.py",
)

DEVICE_UPDATER_HELPER_FILES = (
    "__init__.py",
    "privileged_control.py",
    "business_activation_helper.py",
    "business_activation_candidate_helper.py",
    "business_activation_primitives.py",
    "business_release_activation_candidate_helper.py",
    "mcu_flash_helper.py",
    "mcu_flash_candidate_helper.py",
    "mcu_flash_primitives.py",
    "mcu_flash_recovery.py",
    "updater_mutation_authorizer.py",
)

DEVICE_UPDATER_HELPER_UNIT_FILES = (
    "ecobin-business-activation-helper.socket",
    "ecobin-business-activation-helper@.service",
    "ecobin-mcu-flash-helper.socket",
    "ecobin-mcu-flash-helper@.service",
    "ecobin-business-activation-candidate-helper.socket",
    "ecobin-business-activation-candidate-helper@.service",
    "ecobin-business-release-activation-candidate-helper.socket",
    "ecobin-business-release-activation-candidate-helper@.service",
    "ecobin-mcu-flash-candidate-helper.socket",
    "ecobin-mcu-flash-candidate-helper@.service",
)

# The factory/first-boot application is a separate staged application and
# cannot import from /opt/ecobin/hardware/current/app.  These are the exact
# top-level runtime sources needed by its real systemd entry points.
FACTORY_APP_RUNTIME_FILES = (
    "business_runtime_cutover_state.py",
    "camera_capture.py",
    "camera_selection.py",
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
