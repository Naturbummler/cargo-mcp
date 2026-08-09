import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import server


class ListToolsTest(unittest.TestCase):
    def test_list_tools_are_registered_as_read_only(self):
        tools = asyncio.run(server.app.get_tools())

        for name in ("list_orders", "list_quotations"):
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertTrue(tools[name].annotations.readOnlyHint)

    def test_list_query_parameters_are_bounded_and_mapped(self):
        params = server._list_query_params(
            take=25,
            cursor=100,
            order_by=["sequence"],
            filters=["status=CREATED"],
            filter_operator="or",
            include_total=True,
        )

        self.assertEqual(
            params,
            {
                "take": 25,
                "cursor": 100,
                "orderBy": ["sequence"],
                "filter": ["status=CREATED"],
                "filterOperator": "OR",
                "total": True,
            },
        )

    def test_list_query_rejects_invalid_values(self):
        defaults = {
            "take": 25,
            "cursor": None,
            "order_by": None,
            "filters": None,
            "filter_operator": "AND",
            "include_total": False,
        }

        for override in (
            {"take": 0},
            {"take": 51},
            {"cursor": -1},
            {"filter_operator": "XOR"},
            {"filters": ["status=CREATED\nignored=true"]},
            {"order_by": ["field"] * 11},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    server._list_query_params(**(defaults | override))

    def test_list_tools_call_collection_endpoints(self):
        cases = (
            (server.list_orders, "/v1/orders"),
            (server.list_quotations, "/v1/quotations"),
        )

        for tool, endpoint in cases:
            with self.subTest(tool=tool.name):
                mocked_get = AsyncMock(return_value={"items": []})
                with patch.object(server.CargoboardClient, "get", mocked_get):
                    result = asyncio.run(
                        tool.fn(
                            take=10,
                            cursor=20,
                            order_by=None,
                            filters=None,
                            filter_operator="AND",
                            include_total=False,
                        )
                    )

                self.assertEqual(result, {"items": []})
                mocked_get.assert_awaited_once_with(
                    endpoint,
                    params={"take": 10, "cursor": 20, "total": False},
                )


if __name__ == "__main__":
    unittest.main()
