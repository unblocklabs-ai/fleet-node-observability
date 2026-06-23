from __future__ import annotations

import base64
import unittest
from urllib.parse import unquote

from fleet_node_observability.otlp import basic_auth_header, encode_otlp_header_value, otlp_headers, parse_otlp_headers


class OtlpHeaderTest(unittest.TestCase):
    def test_normalize_auth_header_round_trips(self) -> None:
        encoded = basic_auth_header("mini_03", "token")
        self.assertTrue(encoded.startswith("Basic "))
        decoded = base64.b64decode(encoded.removeprefix("Basic ")).decode("utf-8")
        self.assertEqual(decoded, "mini_03:token")

    def test_encode_otlp_header_values_are_percent_encoded(self) -> None:
        value = "client id=with special,chars%value"
        self.assertEqual(encode_otlp_header_value(value), "client%20id%3Dwith%20special%2Cchars%25value")

    def test_otlp_headers_lan_only_includes_basic(self) -> None:
        headers = otlp_headers(node_label="Bill-Node", token="abc", network="lan")
        parsed = parse_otlp_headers(headers)
        self.assertEqual(parsed.keys(), {"Authorization"})
        decoded_auth = base64.b64decode(parsed["Authorization"].removeprefix("Basic ")).decode("utf-8")
        self.assertEqual(decoded_auth, "bill_node:abc")

    def test_otlp_headers_off_lan_includes_encoded_cloudflare_headers(self) -> None:
        headers = otlp_headers(
            node_label="bill",
            token="abc",
            network="off_lan",
            cf_access_client_id="id with spaces",
            cf_access_client_secret="secret/with=equals",
        )
        pairs = parse_otlp_headers(headers)
        self.assertEqual(pairs["CF-Access-Client-Id"], "id with spaces")
        self.assertEqual(pairs["CF-Access-Client-Secret"], "secret/with=equals")
        self.assertNotIn("id with spaces", headers)
        self.assertNotIn("secret/with=equals", headers)

    def test_otlp_headers_off_lan_requires_both_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "CF_ACCESS_CLIENT_ID"):
            otlp_headers(node_label="bill", token="abc", network="off_lan", cf_access_client_id="id", cf_access_client_secret=None)
