from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contractlib import ContractError  # noqa: E402
from http_contract import (  # noqa: E402
    load_openapi,
    validate_http_contract,
    validate_instance,
    validate_openapi_document,
)


class HttpContractTests(unittest.TestCase):
    def test_authoritative_contract_and_examples_validate(self) -> None:
        checks = validate_http_contract()
        self.assertGreaterEqual(len(checks), 5)

    def test_wallet_read_surface_is_in_authoritative_contract(self) -> None:
        document = load_openapi()
        expected_paths = {
            "/api/v1/miniapp/me/wallet",
            "/api/v1/miniapp/me/wallet/entries",
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/organization-users/{organizationUserUid}/wallet"
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}"
                "/organization-users/{organizationUserUid}/wallet"
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/organization-users/{organizationUserUid}/wallet/entries"
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}"
                "/organization-users/{organizationUserUid}/wallet/entries"
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/wallet-entries"
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}/wallet-entries"
            ),
        }
        missing_paths = expected_paths - set(document["paths"])
        self.assertEqual(
            set(),
            missing_paths,
            f"wallet paths missing from OpenAPI: {sorted(missing_paths)}",
        )

        expected_schemas = {
            "WalletEntryCursor",
            "WalletSummary",
            "WalletSummaryEnvelope",
            "WalletEntryType",
            "WalletEntrySourceType",
            "PersonalWalletEntry",
            "OrganizationWalletEntry",
            "PersonalWalletEntryCursorPage",
            "OrganizationWalletEntryCursorPage",
            "PersonalWalletEntryPageEnvelope",
            "OrganizationWalletEntryPageEnvelope",
        }
        missing_schemas = (
            expected_schemas - set(document["components"]["schemas"])
        )
        self.assertEqual(
            set(),
            missing_schemas,
            f"wallet schemas missing from OpenAPI: {sorted(missing_schemas)}",
        )

    def test_wallet_filter_and_cursor_contract_cannot_drift(self) -> None:
        document = load_openapi()
        changed = copy.deepcopy(document)
        changed["components"]["parameters"]["WalletEntryLimit"]["schema"][
            "maximum"
        ] = 101
        with self.assertRaisesRegex(
            ContractError,
            "wallet entry limit differs",
        ):
            validate_openapi_document(changed)

    def test_funds_controller_surface_and_cursor_queries_are_published(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        organization = "/api/v1/web/organizations/{organizationCode}"
        platform = (
            "/api/v1/web/platform/tenants/{tenantCode}"
            "/organizations/{organizationCode}"
        )
        expected_operations = {
            (organization + "/payout-account", "get"),
            (organization + "/payout-account/entries", "get"),
            (organization + "/recharge-orders", "get"),
            (organization + "/recharge-orders", "post"),
            (organization + "/recharge-orders/{rechargeNo}", "get"),
            (organization + "/withdrawal-configuration", "get"),
            (organization + "/withdrawal-configuration-releases", "post"),
            (organization + "/withdrawals", "get"),
            (organization + "/withdrawals/{withdrawalNo}", "get"),
            (organization + "/withdrawals/{withdrawalNo}/reviews", "post"),
            (
                organization
                + "/withdrawals/{withdrawalNo}/pre-channel-terminations",
                "post",
            ),
            (
                organization + "/withdrawals/{withdrawalNo}/channel-queries",
                "post",
            ),
            (platform + "/payout-account", "get"),
            (platform + "/payout-account/entries", "get"),
            (platform + "/recharge-orders", "get"),
            (platform + "/recharge-orders/{rechargeNo}", "get"),
            (platform + "/withdrawal-configuration", "get"),
            (platform + "/withdrawals", "get"),
            (platform + "/withdrawals/{withdrawalNo}", "get"),
            (platform + "/withdrawals/{withdrawalNo}/reviews", "post"),
            (
                platform
                + "/withdrawals/{withdrawalNo}/pre-channel-terminations",
                "post",
            ),
            (
                platform + "/withdrawals/{withdrawalNo}/channel-queries",
                "post",
            ),
        }
        missing = {
            (path, method)
            for path, method in expected_operations
            if path not in paths or method not in paths[path]
        }
        self.assertEqual(
            set(),
            missing,
            f"funds operations missing from OpenAPI: {sorted(missing)}",
        )

        cursor_collections = {
            organization + "/payout-account/entries",
            organization + "/recharge-orders",
            organization + "/withdrawals",
            platform + "/payout-account/entries",
            platform + "/recharge-orders",
            platform + "/withdrawals",
            "/api/v1/miniapp/me/withdrawals",
        }
        for path in cursor_collections:
            parameters = paths[path]["get"].get("parameters", [])
            names = {
                parameter.get("name")
                for parameter in parameters
                if "$ref" not in parameter
            }
            refs = {parameter.get("$ref") for parameter in parameters}
            self.assertTrue(
                "cursor" in names
                or "#/components/parameters/Cursor" in refs,
                f"funds collection lacks cursor: {path}",
            )

        schemas = document["components"]["schemas"]
        self.assertIn("PayoutEntryPage", schemas)
        self.assertIn("PayoutEntryPageEnvelope", schemas)

    def test_operations_trace_fullness_and_staff_surfaces_are_published(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        expected_operations = {
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/bags/{bagQr}",
                "get",
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/bags/{bagQr}/occupancy-events",
                "get",
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/bags/{bagQr}/clean-records",
                "get",
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/bags/{bagQr}/delivery-orders",
                "get",
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}/bags/{bagQr}",
                "get",
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}/bags/{bagQr}"
                "/occupancy-events",
                "get",
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}/bags/{bagQr}"
                "/clean-records",
                "get",
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}/bags/{bagQr}"
                "/delivery-orders",
                "get",
            ),
            ("/api/v1/miniapp/bags/{bagQr}/use-cycles", "get"),
            (
                "/api/v1/miniapp/bags/{bagQr}/use-cycles/{cycleUid}"
                "/delivery-orders",
                "get",
            ),
            (
                "/api/v1/miniapp/bags/{bagQr}/use-cycles/{cycleUid}"
                "/delivery-orders/{deliveryOrderNo}",
                "get",
            ),
            ("/api/v1/miniapp-staff/devices", "get"),
            (
                "/api/v1/miniapp-staff/devices/{deviceCode}",
                "get",
            ),
            (
                "/api/v1/miniapp-staff/devices/{deviceCode}"
                "/ports/{portNo}/capacity",
                "get",
            ),
            (
                "/api/v1/miniapp-staff/devices/{deviceCode}"
                "/ports/{portNo}/fullness-state/current",
                "get",
            ),
            ("/api/v1/web/platform/operations/reliable-tasks", "get"),
            (
                "/api/v1/web/platform/operations/reliable-tasks/{taskUid}",
                "get",
            ),
            (
                "/api/v1/web/platform/operations/reliable-tasks/{taskUid}"
                "/attempts",
                "get",
            ),
            (
                "/api/v1/web/platform/operations/reliable-tasks/{taskUid}"
                "/resumptions",
                "post",
            ),
            (
                "/api/v1/web/platform/operations/message-quarantines",
                "get",
            ),
            ("/api/v1/web/audit-logs", "get"),
            ("/api/v1/web/platform/audit-logs", "get"),
            ("/api/v1/web/alerts", "get"),
            ("/api/v1/web/alerts/{alertUid}/acknowledgements", "post"),
            ("/api/v1/web/platform/alerts", "get"),
            ("/api/v1/miniapp-staff/alerts", "get"),
            ("/api/v1/web/statistics/operational-overview", "get"),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/statistics/operational-overview",
                "get",
            ),
            (
                "/api/v1/miniapp-staff/statistics/operational-overview",
                "get",
            ),
        }
        missing = {
            (path, method)
            for path, method in expected_operations
            if path not in paths or method not in paths[path]
        }
        self.assertEqual(
            set(),
            missing,
            f"operations surfaces missing from OpenAPI: {sorted(missing)}",
        )

        reconciliation_paths = {
            path for path in paths if "reconciliation-" in path
        }
        self.assertEqual(
            set(),
            reconciliation_paths,
            "I-039 is intentionally deferred and must not be published",
        )

    def test_new_read_models_require_every_serialized_record_field(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        for schema_name in (
            "BagTraceOrder",
            "PortCapacityView",
            "StaffDeviceListEnvelope",
        ):
            schema = schemas[schema_name]
            self.assertEqual(
                set(schema["properties"]),
                set(schema.get("required", [])),
                f"{schema_name} must require every serialized field; "
                "nullable fields stay required and allow null",
            )

        photo_item = schemas["BagTraceOrderDetailEnvelope"]["properties"][
            "data"
        ]["properties"]["photos"]["items"]
        self.assertEqual(
            set(photo_item["properties"]),
            set(photo_item.get("required", [])),
            "BagTracePhoto must require every serialized field",
        )

    def test_device_ownership_and_operation_roles_are_explicit(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        required_methods = {
            "/api/v1/web/platform/device-assets": {"get", "post"},
            "/api/v1/web/platform/device-assets/{hardwareSn}": {"get"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/tenant-assignments"
            ): {"post"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/acceptance-evaluations"
            ): {"post"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/acceptance-evidence"
            ): {"get"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/disablements"
            ): {"post"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/restorations"
            ): {"post"},
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/retirements"
            ): {"post"},
            "/api/v1/web/device-assets": {"get"},
            "/api/v1/web/device-assets/{hardwareSn}": {"get"},
            (
                "/api/v1/web/device-assets/{hardwareSn}"
                "/organization-assignments"
            ): {"post"},
            (
                "/api/v1/web/organizations/{organizationCode}/devices"
            ): {"get"},
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/devices/{deviceCode}"
            ): {"get"},
        }
        for path, expected in required_methods.items():
            self.assertIn(path, paths)
            actual = {
                method for method in paths[path]
                if method in {"get", "post", "put", "patch", "delete"}
            }
            self.assertEqual(expected, actual, path)

        serialized = str(document)
        for legacy_identifier in (
            "device-deployments",
            "deploymentCode",
            "DeviceDeployment",
            "device-asset-allocations",
            "TenantPool",
            "CredentialRotation",
            "MaintenanceClearance",
            "business-switch",
        ):
            self.assertNotIn(legacy_identifier, serialized)

    def test_device_transport_and_runtime_presence_cannot_drift(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        for schema_name in (
            "DeviceAsset",
            "ComputedOneNetMapping",
            "StaffDeviceSummary",
            "StaffPortSummary",
        ):
            schema = schemas[schema_name]
            self.assertEqual(
                set(schema["properties"]),
                set(schema.get("required", [])),
                f"{schema_name} must require every serialized field",
            )

        asset = schemas["DeviceAsset"]
        self.assertIn("deviceCode", asset["properties"])
        self.assertIn("oneNetMapping", asset["properties"])
        self.assertNotIn("tenantAllocation", asset["properties"])
        self.assertNotIn("deploymentProgress", asset["properties"])

        staff_port = schemas["StaffPortSummary"]
        self.assertIn("enabled", staff_port["properties"])
        self.assertNotIn("businessEnabled", staff_port["properties"])
    def test_device_lifecycle_commands_keep_versions_and_blocker_examples(
        self,
    ) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        required_fields = {
            "CreateDeviceAssetRequest": {
                "hardwareSn",
                "modelCode",
                "expectedPortCount",
                "factoryBags",
            },
            "AssignDeviceTenantRequest": {
                "tenantCode",
                "expectedVersion",
            },
            "AssignDeviceOrganizationRequest": {
                "organizationCode",
                "expectedVersion",
            },
            "DeviceControlRequest": {
                "expectedVersion",
                "reason",
            },
            "DeviceConfigurationReleaseRequest": {
                "expectedLatestVersion",
                "locationCorrectionConfirmed",
                "device",
                "ports",
            },
            "DeviceConfigurationResynchronizationRequest": {
                "expectedVersion",
                "reason",
            },
        }
        for name, expected in required_fields.items():
            self.assertEqual(expected, set(schemas[name]["required"]), name)

        paths = document["paths"]
        mutation_paths = {
            "/api/v1/web/platform/device-assets",
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/tenant-assignments"
            ),
            (
                "/api/v1/web/device-assets/{hardwareSn}"
                "/organization-assignments"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/acceptance-evaluations"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/disablements"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/restorations"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/retirements"
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/devices/{deviceCode}/configuration-releases"
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/devices/{deviceCode}/configuration-applications/"
                "{applicationUid}/resynchronizations"
            ),
        }
        for path in mutation_paths:
            operation = paths[path]["post"]
            self.assertIn(
                {"$ref": "#/components/parameters/IdempotencyKey"},
                operation["parameters"],
                path,
            )

    def test_cleaning_controller_surface_is_in_authoritative_contract(
        self,
    ) -> None:
        document = load_openapi()
        paths = document["paths"]
        expected_methods = {
            (
                "/api/v1/miniapp/devices/{deviceCode}"
                "/clean-options"
            ): {"get"},
            "/api/v1/miniapp/clean-operations/{operationUid}": {"get"},
            "/api/v1/miniapp/me/clean-devices": {"get"},
            "/api/v1/miniapp/me/clean-records": {"get"},
            "/api/v1/miniapp/me/clean-records/{cleanRecordNo}": {"get"},
            (
                "/api/v1/miniapp/devices/{deviceCode}"
                "/ports/{portNo}/clean-operations"
            ): {"post"},
        }
        for prefix in (
            "/api/v1/web/organizations/{organizationCode}",
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}"
            ),
        ):
            expected_methods[prefix + "/clean-operations"] = {"get"}
            expected_methods[
                prefix + "/clean-operations/{operationUid}"
            ] = {"get"}
            expected_methods[prefix + "/clean-records"] = {"get"}
            expected_methods[
                prefix + "/clean-records/{cleanRecordNo}"
            ] = {"get", "patch"}
            expected_methods[
                prefix + "/clean-records/{cleanRecordNo}/changes"
            ] = {"get"}

        for path, expected in expected_methods.items():
            self.assertIn(path, paths)
            actual = {
                method
                for method in paths[path]
                if method in {"get", "post", "put", "patch", "delete"}
            }
            self.assertEqual(expected, actual, path)

    def test_organization_miniapp_management_surface_is_exact(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        suffixes = {
            "/miniapp-configuration": {"get", "put"},
            "/miniapp-configuration/activations": {"post"},
            "/miniapp-login/enablements": {"post"},
            "/miniapp-login/disablements": {"post"},
        }
        platform_prefix = (
            "/api/v1/web/platform/tenants/{tenantCode}"
            "/organizations/{organizationCode}"
        )
        for suffix, methods in suffixes.items():
            path = platform_prefix + suffix
            self.assertIn(path, paths)
            self.assertEqual(
                methods,
                {
                    method
                    for method in paths[path]
                    if method in {"get", "put", "post", "delete", "patch"}
                },
            )

        tenant_prefix = "/api/v1/web/organizations/{organizationCode}"
        tenant_read_path = tenant_prefix + "/miniapp-configuration"
        self.assertIn(tenant_read_path, paths)
        self.assertEqual(
            {"get"},
            {
                method
                for method in paths[tenant_read_path]
                if method in {"get", "put", "post", "delete", "patch"}
            },
        )
        for suffix in (
            "/miniapp-configuration/activations",
            "/miniapp-login/enablements",
            "/miniapp-login/disablements",
        ):
            self.assertNotIn(tenant_prefix + suffix, paths)

        schemas = document["components"]["schemas"]
        request_secret = schemas["PutMiniappConfigurationRequest"][
            "properties"
        ]["appSecret"]
        response_secret = schemas["MiniappConfiguration"][
            "properties"
        ]["appSecret"]
        mutation_properties = schemas[
            "MiniappConfigurationMutation"
        ]["properties"]
        self.assertTrue(request_secret["writeOnly"])
        self.assertTrue(response_secret["readOnly"])
        self.assertNotIn("appSecret", mutation_properties)

    def test_web_path_cannot_silently_switch_to_bearer(self) -> None:
        document = load_openapi()
        changed = copy.deepcopy(document)
        changed["paths"]["/api/v1/web/auth/sessions/current"]["get"]["security"] = [
            {"miniappBearer": []}
        ]
        with self.assertRaisesRegex(ContractError, "security"):
            validate_openapi_document(changed)

    def test_web_login_body_cannot_add_client_reported_scope(self) -> None:
        document = load_openapi()
        changed = copy.deepcopy(document)
        changed["components"]["schemas"]["WebLoginRequest"]["properties"][
            "tenantId"
        ] = {"type": "string"}
        with self.assertRaisesRegex(ContractError, "only submit"):
            validate_openapi_document(changed)

    def test_web_session_organization_name_matches_runtime_view(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        organization_ref = schemas["WebSession"]["properties"][
            "organizations"
        ]["items"]["$ref"]
        self.assertEqual(
            organization_ref,
            "#/components/schemas/WebOrganizationSummary",
        )
        web_summary = schemas["WebOrganizationSummary"]
        self.assertIn("organizationName", web_summary["required"])
        self.assertNotIn("displayName", web_summary["properties"])
        self.assertEqual(
            schemas["MiniappSessionView"]["properties"]["organization"][
                "$ref"
            ],
            "#/components/schemas/OrganizationSummary",
        )

    def test_decimal_schema_rejects_json_number(self) -> None:
        document = load_openapi()
        schema = document["components"]["schemas"]["MoneyCny"]
        with self.assertRaisesRegex(ContractError, "expected string"):
            validate_instance(12.3, schema, document)

    def test_problem_detail_requires_stable_error_code(self) -> None:
        document = load_openapi()
        schema = document["components"]["schemas"]["ProblemDetail"]
        invalid = {
            "code": "设备忙",
            "message": "设备忙",
            "requestId": "request-1",
            "retryable": False,
            "details": {},
        }
        with self.assertRaisesRegex(ContractError, "pattern"):
            validate_instance(invalid, schema, document)

    def test_public_uid_rejects_uuid_v7(self) -> None:
        document = load_openapi()
        schema = document["components"]["schemas"]["PublicUid"]
        with self.assertRaisesRegex(ContractError, "pattern"):
            validate_instance(
                "01906b50-2b4f-7f09-8d7d-7fc1c01a0001",
                schema,
                document,
            )

    def test_operation_and_session_ids_use_uuid_v4(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        self.assertEqual(
            schemas["PublicUid"]["allOf"][0]["$ref"],
            "#/components/schemas/UuidV4",
        )
        self.assertEqual(
            schemas["AcceptedOperation"]["properties"]["operationId"]["$ref"],
            "#/components/schemas/UuidV4",
        )
        self.assertEqual(
            schemas["WebSession"]["properties"]["sessionUid"]["$ref"],
            "#/components/schemas/UuidV4",
        )

    def test_miniapp_created_and_current_session_models_are_distinct(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        created = schemas["MiniappSessionCreated"]
        current = schemas["MiniappSessionView"]

        self.assertIn("accessToken", created["required"])
        self.assertTrue(created["properties"]["accessToken"]["readOnly"])
        self.assertNotIn("writeOnly", created["properties"]["accessToken"])
        self.assertNotIn("accessToken", current["properties"])
        self.assertNotIn("tokenType", current["properties"])
        self.assertNotIn("isNewRegistration", current["properties"])

        responses = document["components"]["responses"]
        created_ref = responses["MiniappSessionCreated"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        current_ref = responses["MiniappSessionCurrent"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        self.assertEqual(
            created_ref,
            "#/components/schemas/MiniappSessionCreatedEnvelope",
        )
        self.assertEqual(
            current_ref,
            "#/components/schemas/MiniappSessionViewEnvelope",
        )

    def test_miniapp_delivery_orders_use_owned_safe_no_store_models(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        responses = document["components"]["responses"]
        schemas = document["components"]["schemas"]

        list_operation = paths[
            "/api/v1/miniapp/me/delivery-orders"
        ]["get"]
        detail_operation = paths[
            "/api/v1/miniapp/me/delivery-orders/{deliveryOrderNo}"
        ]["get"]
        for operation in (list_operation, detail_operation):
            self.assertEqual(
                operation["security"],
                [{"miniappBearer": []}],
            )

        self.assertEqual(
            list_operation["responses"]["200"]["$ref"],
            "#/components/responses/MiniappDeliveryOrderPageOk",
        )
        self.assertEqual(
            detail_operation["responses"]["200"]["$ref"],
            "#/components/responses/MiniappDeliveryOrderDetailOk",
        )
        self.assertEqual(
            list_operation["responses"]["400"]["$ref"],
            "#/components/responses/MiniappPrivateReadInvalidRequest",
        )
        self.assertEqual(
            list_operation["responses"]["401"]["$ref"],
            "#/components/responses/MiniappPrivateReadUnauthorizedProblem",
        )
        self.assertEqual(
            list_operation["responses"]["500"]["$ref"],
            "#/components/responses/MiniappPrivateReadInternalProblem",
        )
        self.assertEqual(
            detail_operation["responses"]["400"]["$ref"],
            "#/components/responses/MiniappPrivateReadInvalidRequest",
        )
        self.assertEqual(
            detail_operation["responses"]["401"]["$ref"],
            "#/components/responses/MiniappPrivateReadUnauthorizedProblem",
        )
        self.assertEqual(
            detail_operation["responses"]["404"]["$ref"],
            "#/components/responses/MiniappPrivateReadNotFoundProblem",
        )
        self.assertEqual(
            detail_operation["responses"]["500"]["$ref"],
            "#/components/responses/MiniappPrivateReadInternalProblem",
        )
        for response_name in (
            "MiniappDeliveryOrderPageOk",
            "MiniappDeliveryOrderDetailOk",
            "MiniappPrivateReadInvalidRequest",
            "MiniappPrivateReadUnauthorizedProblem",
            "MiniappPrivateReadNotFoundProblem",
            "MiniappPrivateReadInternalProblem",
        ):
            self.assertEqual(
                responses[response_name]["headers"]["Cache-Control"]["$ref"],
                "#/components/headers/NoStore",
            )
            self.assertEqual(
                responses[response_name]["headers"]["X-Request-Id"]["$ref"],
                "#/components/headers/RequestId",
            )

        item_properties = schemas["MiniappDeliveryOrderItem"]["properties"]
        detail_properties = schemas[
            "MiniappDeliveryOrderDetail"
        ]["properties"]
        anomaly_properties = schemas[
            "MiniappDeliveryAnomaly"
        ]["properties"]
        self.assertNotIn("organizationUserUid", item_properties)
        self.assertNotIn("ownership", detail_properties)
        self.assertNotIn("revisions", detail_properties)
        self.assertNotIn("diagnosticDetails", anomaly_properties)

    def test_miniapp_cleaning_normal_path_contract_is_complete(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        expected = {
            "/api/v1/miniapp/devices/{deviceCode}/clean-options",
            (
                "/api/v1/miniapp/devices/{deviceCode}"
                "/ports/{portNo}/clean-operations"
            ),
            "/api/v1/miniapp/clean-operations/{operationUid}",
            "/api/v1/miniapp/me/clean-devices",
            "/api/v1/miniapp/me/clean-records",
            "/api/v1/miniapp/me/clean-records/{cleanRecordNo}",
        }
        self.assertEqual(set(), expected - set(paths))

        for path in expected:
            method = "post" if path.endswith("/clean-operations") else "get"
            self.assertEqual(
                paths[path][method]["security"],
                [{"miniappBearer": []}],
            )

        start_path = (
            "/api/v1/miniapp/devices/{deviceCode}"
            "/ports/{portNo}/clean-operations"
        )
        start = paths[start_path]["post"]
        parameter_refs = {
            item["$ref"] for item in start["parameters"]
        }
        self.assertIn(
            "#/components/parameters/IdempotencyKey",
            parameter_refs,
        )
        self.assertEqual(
            start["responses"]["202"]["$ref"],
            "#/components/responses/CleanOperationAcceptedResponse",
        )
        accepted = document["components"]["responses"][
            "CleanOperationAcceptedResponse"
        ]
        self.assertEqual(
            accepted["headers"]["Location"]["$ref"],
            "#/components/headers/Location",
        )

        list_operation = paths[
            "/api/v1/miniapp/me/clean-records"
        ]["get"]
        self.assertIn(
            {"$ref": "#/components/parameters/Cursor"},
            list_operation["parameters"],
        )
        limit = next(
            item
            for item in list_operation["parameters"]
            if item.get("name") == "limit"
        )
        self.assertEqual(limit["schema"]["minimum"], 1)
        self.assertEqual(limit["schema"]["maximum"], 100)
        device_list = paths[
            "/api/v1/miniapp/me/clean-devices"
        ]["get"]
        self.assertIn(
            {"$ref": "#/components/parameters/Cursor"},
            device_list["parameters"],
        )
        device_filter = next(
            item
            for item in device_list["parameters"]
            if item.get("name") == "filter"
        )
        self.assertTrue(device_filter["required"])
        self.assertEqual(
            device_filter["schema"]["$ref"],
            "#/components/schemas/CleanDeviceFilter",
        )
        schemas = document["components"]["schemas"]
        for schema in (
            "CleanDeviceFilter",
            "CleanDeviceItem",
            "CleanDeviceCursorPage",
            "CleanOptions",
            "CleanOptionBlocker",
            "CleanPortOption",
            "CleanOperationAccepted",
            "CleanOperation",
            "CleanRecordCursorPage",
            "CleanRecordItem",
            "MiniappCleanRecordDetail",
        ):
            self.assertIn(schema, schemas)
        for schema in (
            "CleanDeviceItem",
            "CleanOptions",
            "CleanOperation",
            "CleanRecordItem",
            "CleanRecordSource",
        ):
            self.assertIn("deviceCode", schemas[schema]["required"])
            self.assertIn("deviceCode", schemas[schema]["properties"])
            self.assertNotIn(
                "deploymentCode",
                schemas[schema]["properties"],
            )
        self.assertEqual(
            schemas["CleanDeviceFilter"]["enum"],
            [
                "ALL",
                "ONLINE",
                "NO_DELIVERY_24H",
                "NO_CLEAN_24H",
                "FULL",
                "FULL_TIMEOUT_2H",
            ],
        )
        self.assertEqual(
            schemas["CleanOptionBlocker"]["enum"],
            [
                "CLEAN_CONFIGURATION_UNAVAILABLE",
                "CONFIGURATION_NOT_APPLIED",
                "EDGE_OFFLINE",
                "DEVICE_BUSY",
                "PORT_DISABLED",
                "CLEAN_OPERATION_ACTIVE",
                "PORT_WORK_ACTIVE",
            ],
        )
        installed_bag = schemas["StartCleanOperationRequest"][
            "properties"
        ]["installedBagQr"]
        if "$ref" in installed_bag:
            installed_bag = schemas[
                installed_bag["$ref"].rsplit("/", 1)[-1]
            ]
        self.assertEqual(installed_bag["minLength"], 54)
        self.assertEqual(installed_bag["maxLength"], 59)
        self.assertTrue(installed_bag["pattern"].startswith("^EB1_"))

    def test_web_clean_operation_contract_uses_canonical_states(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        for path in (
            "/api/v1/web/organizations/{organizationCode}/clean-operations",
            "/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/clean-operations",
        ):
            self.assertIn("get", paths[path])
        statuses = document["components"]["schemas"][
            "CleanOperationStatus"
        ]["enum"]
        self.assertEqual(
            statuses,
            [
                "PREPARED",
                "EDGE_SAVED",
                "IN_PROGRESS",
                "RECOVERY_REQUIRED",
                "PRE_UNLOCK_ENDED",
                "COMPLETED",
                "ABORTED",
            ],
        )
        self.assertNotIn("PRE_OPEN_ENDED", statuses)

    def test_legacy_bearer_cutover_requires_client_cleanup_and_server_revoke(
        self,
    ) -> None:
        document = load_openapi()
        changed = copy.deepcopy(document)
        changed["x-ecobin-legacy-credential-cutover"][
            "serverLegacyBearerPolicy"
        ] = ""
        with self.assertRaisesRegex(ContractError, "server invalidation"):
            validate_openapi_document(changed)


if __name__ == "__main__":
    unittest.main()
