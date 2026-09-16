"""Canonical source inventory and schema declaration for runtime releases.

Keep this module dependency-free: it is imported by both the signed runtime
release tooling and the immutable image builder.
"""

from __future__ import annotations


EDGE_SCHEMA_VERSION = "40"


def verify_source_schema_version(source_root):
    """Check the source declaration without importing or executing the app.

    Run before platform/dependency/signing work. A source tree with a newer
    database version must not acquire this builder's older release label.
    This check does not approve a protocol, a backend allowlist or a release.
    """
    import ast

    try:
        tree = ast.parse((source_root / "edge_store.py").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise RuntimeError("cannot read source schema declaration in edge_store.py") from error
    values = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "CURRENT_SCHEMA_VERSION" for target in targets):
            values.append(node.value)
    if (len(values) != 1 or not isinstance(values[0], ast.Constant)
            or type(values[0].value) is not int or values[0].value <= 0):
        raise RuntimeError("source schema declaration must be one positive integer literal")
    actual = values[0].value
    if str(actual) != EDGE_SCHEMA_VERSION:
        raise RuntimeError(f"source schema {actual} differs from release manifest {EDGE_SCHEMA_VERSION}")
    return actual


RUNTIME_APP_FILES = (
    "bounded_worker.py",
    "uart2_protocol.py",
    "mcu_action_evidence.py",
    "work_recovery.py",
    "native_result_evidence.py",
    "native_result_report.py",
    "native_delivery_issue_report.py",
    "native_device_entry_url.py",
    "native_delivery_recovery_close.py",
    "native_recovery_close_isolation.py",
    "native_recovery_entry.py",
    "native_recovery_runtime.py",
    "native_business_completion.py",
    "native_business_runtime.py",
    "native_configuration_reload.py",
    "native_control_failure.py",
    "native_issue_completion.py",
    "native_job_rpc.py",
    "mcu_work_query.py",
    "mcu_actuator_handoff.py",
    "mcu_process_handoff.py",
    "mcu_session.py",
    "mcu_result_handoff.py",
    "mcu_configuration.py",
    "uart2_transport.py",
    "uart_request_tracker.py",
    "business_identity.py",
    "business_control.py",
    "native_fault_control_cli.py",
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
    "fullness_transition.py",
    "fixed_frame_health_recovery.py",
    "fixed_frame_mcu_adapter.py",
    "fixed_frame_mcu_maintenance.py",
    "factory_progress.py",
    "factory_seal/__init__.py",
    "factory_seal/admission.py",
    "factory_seal/errors.py",
    "factory_seal/runtime.py",
    "factory_seal/validation.py",
    "factory_seal/weight_validation.py",
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
    "job_safety.py",
    "uart2_protocol.py",
    "business_runtime_cutover.py",
    "business_runtime_cutover_state.py",
    "business_update_coordinator.py",
    "business_update_downloader.py",
    "business_update_reporter.py",
    "business_update_package.py",
    "business_update_store.py",
    "device_software_state_reporter.py",
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
    "onenet_projection_model.json",
    "onenet_wire.py",
    "trusted_clock.py",
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
    "mcu_configuration.py",
    "onenet_projection_model.json",
    "onenet_wire.py",
    "simulated_camera.py",
    "system/__init__.py",
    "system/mcu_safe_gpio.py",
    "trusted_clock.py",
    "uart_link.py",
    "uart_protocol.py",
    "uart2_protocol.py",
    "uart2_transport.py",
    "uart_request_tracker.py",
)
