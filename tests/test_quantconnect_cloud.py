import base64
import hashlib
import unittest
from unittest.mock import Mock

from quantconnect_cloud import (
    QuantConnectCloud,
    QuantConnectConfig,
    QuantConnectError,
    quantconnect_client_from_env,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class QuantConnectCloudTests(unittest.TestCase):
    def setUp(self):
        self.config = QuantConnectConfig(
            user_id="123", api_token="secret-token", project_id=456,
            base_url="https://example.invalid/api/v2",
        )
        self.session = Mock()
        self.client = QuantConnectCloud(
            self.config, session=self.session, clock=lambda: 1700000000
        )

    def test_auth_header_matches_documented_timestamp_hash(self):
        timestamp = "1700000000"
        token = hashlib.sha256(
            f"secret-token:{timestamp}".encode("utf-8")
        ).hexdigest()
        expected = base64.b64encode(
            f"123:{token}".encode("utf-8")
        ).decode("ascii")
        self.assertEqual(
            self.client._headers(),
            {"Authorization": f"Basic {expected}", "Timestamp": timestamp},
        )

    def test_submit_compiles_then_creates_backtest(self):
        self.session.post.side_effect = [
            FakeResponse({"success": True, "compileId": "compile-1"}),
            FakeResponse({"success": True, "backtest": {"backtestId": "bt-1"}}),
        ]
        result = self.client.submit_backtest("gold 5m", {"symbol": "XAUUSD"})
        self.assertEqual(result, {"compile_id": "compile-1", "backtest_id": "bt-1"})
        self.assertEqual(self.session.post.call_count, 2)
        self.assertEqual(
            self.session.post.call_args_list[1].kwargs["json"],
            {
                "projectId": 456,
                "compileId": "compile-1",
                "backtestName": "gold 5m",
                "parameters": {"symbol": "XAUUSD"},
            },
        )

    def test_rejected_request_raises_without_exposing_token(self):
        self.session.post.return_value = FakeResponse(
            {"success": False, "errors": ["invalid project"]}
        )
        with self.assertRaisesRegex(QuantConnectError, "invalid project") as caught:
            self.client.authenticate()
        self.assertNotIn("secret-token", str(caught.exception))

    def test_missing_configuration_disables_client(self):
        self.assertIsNone(quantconnect_client_from_env({}))

    def test_invalid_project_id_is_rejected(self):
        with self.assertRaises(ValueError):
            QuantConnectConfig.from_env({
                "QC_USER_ID": "123",
                "QC_API_TOKEN": "token",
                "QC_PROJECT_ID": "not-an-integer",
            })


if __name__ == "__main__":
    unittest.main()
