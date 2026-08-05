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

    def test_device_ownership_and_operation_roles_are_explicit(self) -> None:
        document = load_openapi()
        paths = document["paths"]
        organization_collection = (
            "/api/v1/web/organizations/{organizationCode}"
            "/device-deployments"
        )
        platform_collection = (
            "/api/v1/web/platform/tenants/{tenantCode}"
            "/organizations/{organizationCode}/device-deployments"
        )
        self.assertIn("post", paths[organization_collection])
        self.assertNotIn("post", paths[platform_collection])

        required = {
            "/api/v1/web/platform/device-assets/{hardwareSn}",
            "/api/v1/web/device-asset-allocations",
            "/api/v1/web/platform/device-asset-allocations",
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/device-asset-allocations"
            ),
            (
                organization_collection
                + "/{deploymentCode}/returns-to-tenant-pool"
            ),
            (
                "/api/v1/web/platform/device-asset-allocations"
                "/{allocationUid}/reclaims"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/onenet-credential-rotation-confirmations"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/maintenance-clearances"
            ),
            (
                platform_collection
                + "/{deploymentCode}/acceptance-readiness"
            ),
            platform_collection + "/{deploymentCode}/acceptances",
            platform_collection + "/{deploymentCode}/technical-suspensions",
        }
        self.assertEqual(set(), required - set(paths))

        self.assertIn("post", paths[organization_collection])
        self.assertNotIn("post", paths[platform_collection])
        self.assertEqual(
            paths[organization_collection]["post"]["requestBody"]
            ["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/CreateAllocatedDeviceDeploymentRequest",
        )

        stale = {
            organization_collection + "/{deploymentCode}/activations",
            organization_collection + "/{deploymentCode}/deactivations",
            platform_collection + "/{deploymentCode}/activations",
            platform_collection + "/{deploymentCode}/deactivations",
            platform_collection
            + "/{deploymentCode}/business-switch/enablements",
            platform_collection
            + "/{deploymentCode}/business-switch/disablements",
        }
        self.assertEqual(set(), stale & set(paths))

    def test_device_transport_and_runtime_presence_cannot_drift(self) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        deployment = schemas["DeviceDeployment"]
        runtime_health = schemas["DeviceRuntimeHealthSummary"]
        expected_presence = {
            "oneNetConnectionStatus",
            "oneNetStatusObservedAt",
            "trustedRuntimeReceivedAt",
        }
        self.assertEqual(
            set(),
            expected_presence - set(deployment["required"]),
        )
        self.assertEqual(
            set(),
            expected_presence - set(deployment["properties"]),
        )
        self.assertEqual(
            set(),
            expected_presence - set(runtime_health["required"]),
        )
        self.assertEqual(
            set(),
            expected_presence - set(runtime_health["properties"]),
        )

        for path in (
            "/api/v1/web/organizations/{organizationCode}"
            "/device-deployments",
            "/api/v1/web/platform/tenants/{tenantCode}"
            "/organizations/{organizationCode}/device-deployments",
        ):
            query_names = {
                parameter["name"]
                for parameter in document["paths"][path]["get"]["parameters"]
                if parameter.get("in") == "query"
            }
            self.assertIn("oneNetConnectionStatus", query_names)
    def test_device_lifecycle_commands_keep_versions_and_blocker_examples(
        self,
    ) -> None:
        document = load_openapi()
        schemas = document["components"]["schemas"]
        required_fields = {
            "CreateDeviceTenantAllocationRequest": {
                "hardwareSn",
                "expectedAssetVersion",
            },
            "CreateAllocatedDeviceDeploymentRequest": {
                "allocationUid",
                "expectedAllocationVersion",
            },
            "ReturnDeviceDeploymentToTenantPoolRequest": {
                "expectedDeploymentVersion",
                "expectedAllocationVersion",
                "reason",
            },
            "ReclaimDeviceTenantAllocationRequest": {
                "expectedAllocationVersion",
                "expectedAssetVersion",
                "mode",
                "physicalPossessionConfirmed",
                "reason",
            },
            "ConfirmOneNetCredentialRotationRequest": {
                "expectedAssetVersion",
                "reason",
            },
            "ClearDeviceMaintenanceRequest": {
                "expectedAssetVersion",
                "physicalPossessionConfirmed",
                "inspectionConfirmed",
                "reason",
            },
            "AcceptDeviceDeploymentRequest": {
                "expectedDeploymentVersion",
                "expectedConfigurationVersion",
                "deliveryDoorObservedNormal",
                "camerasObservedNormal",
                "cleanDoorInstallationObservedNormal",
            },
        }
        for name, expected in required_fields.items():
            self.assertEqual(expected, set(schemas[name]["required"]), name)
            self.assertIn("example", schemas[name], name)

        paths = document["paths"]
        mutation_paths = {
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/device-asset-allocations"
            ),
            (
                "/api/v1/web/platform/device-asset-allocations"
                "/{allocationUid}/reclaims"
            ),
            (
                "/api/v1/web/organizations/{organizationCode}"
                "/device-deployments/{deploymentCode}"
                "/returns-to-tenant-pool"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/onenet-credential-rotation-confirmations"
            ),
            (
                "/api/v1/web/platform/device-assets/{hardwareSn}"
                "/maintenance-clearances"
            ),
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}"
                "/device-deployments/{deploymentCode}/acceptances"
            ),
        }
        for path in mutation_paths:
            operation = paths[path]["post"]
            self.assertIn(
                {"$ref": "#/components/parameters/IdempotencyKey"},
                operation["parameters"],
                path,
            )
            self.assertEqual(
                operation["responses"]["422"]["$ref"],
                "#/components/responses/BusinessRuleProblem",
                path,
            )

        examples = document["components"]["responses"][
            "BusinessRuleProblem"
        ]["content"]["application/problem+json"]["examples"]
        self.assertEqual(
            examples["credentialRotationRequired"]["value"]["code"],
            "DEVICE.CREDENTIAL_ROTATION_REQUIRED",
        )
        for example in examples.values():
            blockers = example["value"]["details"].get("blockers")
            self.assertIsInstance(blockers, list)
            self.assertTrue(blockers)

    def test_cleaning_controller_surface_is_in_authoritative_contract(
        self,
    ) -> None:
        document = load_openapi()
        paths = document["paths"]
        expected_methods = {
            (
                "/api/v1/miniapp/device-deployments/{deploymentCode}"
                "/clean-options"
            ): {"get"},
            "/api/v1/miniapp/clean-operations/{operationUid}": {"get"},
            "/api/v1/miniapp/me/clean-records": {"get"},
            "/api/v1/miniapp/me/clean-records/{cleanRecordNo}": {"get"},
            (
                "/api/v1/miniapp/device-deployments/{deploymentCode}"
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
        prefixes = (
            (
                "/api/v1/web/platform/tenants/{tenantCode}"
                "/organizations/{organizationCode}"
            ),
            "/api/v1/web/organizations/{organizationCode}",
        )
        for prefix in prefixes:
            for suffix, methods in suffixes.items():
                path = prefix + suffix
                self.assertIn(path, paths)
                self.assertEqual(
                    methods,
                    {
                        method
                        for method in paths[path]
                        if method in {"get", "put", "post", "delete", "patch"}
                    },
                )

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
