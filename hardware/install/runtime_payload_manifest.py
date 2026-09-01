"""Canonical source inventory and schema declaration for runtime releases.

Keep this module dependency-free: it is imported by both the signed runtime
release tooling and the immutable image builder.
"""

from __future__ import annotations


EDGE_SCHEMA_VERSION = "18"

RUNTIME_APP_FILES = (
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
