import json
import unittest
import urllib.error

from netwidget import providers

IPWHO = json.dumps({"ip": "203.0.113.10", "success": True, "country": "Japan", "country_code": "JP",
                    "region": "Tokyo", "city": "Tokyo",
                    "connection": {"asn": 64500, "org": "Example Org", "isp": "ExampleISP"},
                    "timezone": {"id": "Asia/Tokyo"}})
IPAPI = json.dumps({"status": "success", "country": "Japan", "countryCode": "JP", "regionName": "Tokyo",
                    "city": "Tokyo", "isp": "ExampleISP", "org": "Example Org", "as": "AS64500 Example",
                    "timezone": "Asia/Tokyo", "query": "203.0.113.10"})
IPINFO = json.dumps({"ip": "203.0.113.10", "city": "Tokyo", "region": "Tokyo", "country": "JP",
                     "org": "AS64500 ExampleISP", "timezone": "Asia/Tokyo"})


class FakeHttp:
    """按 URL 子串返回预设响应；值为 Exception 时抛出。记录调用顺序。"""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        for key, value in self.routes.items():
            if key in url:
                if isinstance(value, BaseException):
                    raise value
                return value
        raise urllib.error.URLError("no route for %s" % url)


def http_429(url):
    return urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)


class ParserTest(unittest.TestCase):
    def test_ipwhois(self):
        g = providers.parse_ipwhois(IPWHO)
        self.assertEqual((g["ip"], g["country_code"], g["asn"], g["timezone"], g["isp"]),
                         ("203.0.113.10", "JP", "AS64500", "Asia/Tokyo", "ExampleISP"))

    def test_ipapi(self):
        g = providers.parse_ipapi(IPAPI)
        self.assertEqual((g["ip"], g["asn"], g["region"]), ("203.0.113.10", "AS64500", "Tokyo"))

    def test_ipinfo(self):
        g = providers.parse_ipinfo(IPINFO)
        self.assertEqual((g["asn"], g["isp"], g["country_code"]), ("AS64500", "ExampleISP", "JP"))

    def test_failures_raise(self):
        with self.assertRaises(providers.ProviderError):
            providers.parse_ipwhois(json.dumps({"success": False, "message": "rate limit"}))
        with self.assertRaises(providers.ProviderError):
            providers.parse_ipapi(json.dumps({"status": "fail", "message": "quota"}))
        with self.assertRaises(providers.ProviderError):
            providers.parse_ipinfo("<html>rate limited</html>")
        with self.assertRaises(providers.ProviderError):
            providers.parse_ipify(json.dumps({"ip": "999.1.1.1"}))

    def test_plain_ip(self):
        self.assertEqual(providers.parse_plain_ip("203.0.113.10\n"), "203.0.113.10")


class FallbackTest(unittest.TestCase):
    def test_discover_first_wins(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"})})
        ip, name, attempts = providers.discover_ip(http)
        self.assertEqual((ip, name, len(attempts)), ("203.0.113.10", "ipify", 1))

    def test_discover_falls_back(self):
        http = FakeHttp({"ipify": http_429("x"), "checkip.amazonaws.com": "203.0.113.10\n"})
        ip, name, attempts = providers.discover_ip(http)
        self.assertEqual((ip, name), ("203.0.113.10", "aws-checkip"))
        self.assertEqual(attempts[0], {"provider": "ipify", "ok": False, "error": "HTTP 429"})

    def test_discover_all_fail(self):
        ip, name, attempts = providers.discover_ip(FakeHttp({}))
        self.assertIsNone(ip)
        self.assertEqual([a["ok"] for a in attempts], [False, False])

    def test_geo_rate_limited_then_next(self):
        http = FakeHttp({"ipwho.is": json.dumps({"success": False, "message": "limit"}),
                         "ip-api.com": http_429("x"), "ipinfo.io": IPINFO})
        geo, attempts = providers.lookup_geo("203.0.113.10", http)
        self.assertEqual(geo["provider"], "ipinfo.io")
        self.assertEqual([a["ok"] for a in attempts], [False, False, True])
        self.assertIn("ipwho.is/203.0.113.10", http.calls[0])
        self.assertIn("ip-api.com/json/203.0.113.10", http.calls[1])

    def test_geo_self_lookup(self):
        http = FakeHttp({"ipwho.is": IPWHO})
        geo, _ = providers.lookup_geo(None, http)
        self.assertEqual(http.calls, ["https://ipwho.is/"])
        self.assertEqual(geo["ip"], "203.0.113.10")

    def test_geo_ip_mismatch_rejected(self):
        http = FakeHttp({"ipwho.is": IPWHO, "ip-api.com": IPAPI.replace("203.0.113.10", "198.51.100.1"),
                         "ipinfo.io": IPINFO.replace("203.0.113.10", "198.51.100.1")})
        geo, attempts = providers.lookup_geo("198.51.100.1", http)
        self.assertEqual(geo["provider"], "ip-api.com")
        self.assertFalse(attempts[0]["ok"])

    def test_geo_all_fail(self):
        geo, attempts = providers.lookup_geo("203.0.113.10", FakeHttp({}))
        self.assertIsNone(geo)
        self.assertEqual(len(attempts), 3)
