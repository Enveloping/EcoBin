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
