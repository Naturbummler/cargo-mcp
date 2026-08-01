import asyncio
import importlib
import os
import unittest

from starlette.testclient import TestClient


class CargoboardServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["CARGOBOARD_API_KEY"] = "test-api-key"
        os.environ["CARGOBOARD_BASE_URL"] = "https://example.invalid"
        os.environ["CARGOBOARD_ENABLE_WRITE_TOOLS"] = "false"
        os.environ["ENTRA_CLIENT_ID"] = "11111111-1111-1111-1111-111111111111"
        os.environ["ENTRA_CLIENT_SECRET"] = "local-test-client-secret"
        os.environ["ENTRA_TENANT_ID"] = "22222222-2222-2222-2222-222222222222"
        os.environ["ENTRA_SCOPE"] = "mcp.access"
        os.environ["MCP_PUBLIC_BASE_URL"] = "http://localhost:8000"
        os.environ["OAUTH_JWT_SIGNING_KEY"] = "local-test-signing-key-0123456789abcdef"
        os.environ["OAUTH_STORAGE_BACKEND"] = "memory"
        cls.server = importlib.import_module("server")
        cls.client = TestClient(
            cls.server.app.http_app(path="/mcp", stateless_http=True)
        )

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_health_is_public_and_minimal(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_mcp_endpoint_requires_oauth(self):
        self.assertEqual(self.client.get("/").status_code, 404)
        response = self.client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers.get("www-authenticate", ""))

    def test_order_creation_is_disabled_by_default(self):
        tools = {
            tool.name: tool
            for tool in asyncio.run(self.server.app._list_tools_mcp())
        }

        self.assertIn("create_quotation", tools)
        self.assertIn("get_quotation", tools)
        self.assertIn("get_order", tools)
        self.assertIn("print_shipment_labels", tools)
        self.assertNotIn("create_order", tools)

    def test_oauth_discovery_is_public_but_does_not_expose_tools(self):
        response = self.client.get("/.well-known/oauth-protected-resource/mcp")

        self.assertEqual(response.status_code, 200)
        metadata = response.json()
        self.assertEqual(metadata["resource"], "http://localhost:8000/mcp")
        self.assertNotIn("tools", metadata)

    def test_resource_ids_cannot_change_the_upstream_path(self):
        with self.assertRaisesRegex(ValueError, "Invalid identifier"):
            self.server._validated_resource_id("../orders/other")

if __name__ == "__main__":
    unittest.main()
