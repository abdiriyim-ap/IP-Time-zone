import json
import os
import shutil
import tempfile
import unittest

from netwidget import app, cli, core
from netwidget.config import validate

from .test_providers import IPINFO, IPWHO, FakeHttp, http_429


class FakeSys:
    IS_MAC = True

    def __init__(self):
        self.rx = 1000
        self.tx = 500
        self.notes = []
        self.qx = True
        self.tuns = [{"iface": "utun4", "ipv4": "198.18.0.1"}]
        self.names = ["en0"]

    def sample_counters(self):
        self.rx += 10240
        self.tx += 2048
        return {"ts": self.clock(), "rx": self.rx, "tx": self.tx, "ifaces": ["en0"], "names": self.names}

    def system_timezone(self):
        return "Asia/Shanghai"

    def quantumult_x_running(self):
        return self.qx

    def tun_ipv4(self):
        return self.tuns

    def notify(self, title, msg):
        self.notes.append((title, msg))
        return True


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "exits.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"exits": [{"ip": "203.0.113.10", "label": "东京-固定A", "expect_tz": "Asia/Tokyo"},
                                 {"ip": "198.51.100.22", "label": "新加坡-固定B"}],
                       "real_ip_hints": ["192.0.2.0/24"]}, f, ensure_ascii=False)
        self.paths = app.Paths(self.cfg, os.path.join(self.tmp, "cache"), os.path.join(self.tmp, "state"))
        self.t = [1_800_000_000.0]
        self.sys = FakeSys()
        self.sys.clock = lambda: self.t[0]
        self.slept = []

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_once(self, http, **kw):
        def sleep(s):
            self.slept.append(s)
            self.t[0] += s
        return app.collect(self.paths, get=http, sysmod=self.sys, clock=lambda: self.t[0], sleep=sleep, **kw)

    def test_green_first_run_samples_speed(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        snap = self.run_once(http)
        self.assertEqual(snap["status"]["level"], core.GREEN)
        self.assertEqual(snap["status"]["label"], "东京-固定A")
        self.assertEqual(self.slept, [1.0])
        self.assertAlmostEqual(snap["speed"]["down_bps"], 10240.0)
        self.assertEqual(snap["timezones"]["exit"]["tz"], "Asia/Tokyo")
        self.assertTrue(snap["timezones"]["mismatch"])
        self.assertFalse(snap["signals"]["proxy_likely_off"])
        self.assertEqual(self.sys.notes, [])  # 白名单命中且是首次，不打扰
        with open(self.paths.history, encoding="utf-8") as f:
            self.assertIn("首次记录 → 203.0.113.10(东京-固定A)", f.read())

    def test_ip_cached_for_60s_and_geo_for_24h(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        self.run_once(http)
        n = len(http.calls)
        self.t[0] += 10
        snap = self.run_once(http)
        self.assertEqual(len(http.calls), n)          # 60 秒内不再联网
        self.assertFalse(snap["source"]["refreshed"])
        self.assertEqual(self.slept, [1.0])           # 第二次用上次的计数算网速
        self.assertAlmostEqual(snap["speed"]["down_bps"], 1024.0)
        self.t[0] += 60
        snap = self.run_once(http)
        self.assertTrue(snap["source"]["refreshed"])
        self.assertEqual(http.calls[n:], ["https://api.ipify.org?format=json"])  # 地理信息走缓存
        self.assertTrue(snap["source"]["geo_provider"].startswith("缓存"))

    def test_exit_change_notifies_once_and_logs(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        self.run_once(http)
        self.t[0] += 61
        http2 = FakeHttp({"ipify": json.dumps({"ip": "198.51.100.22"}),
                          "ipwho.is": IPWHO.replace("203.0.113.10", "198.51.100.22")})
        snap = self.run_once(http2)
        self.assertEqual(snap["status"]["label"], "新加坡-固定B")
        self.assertEqual(len(self.sys.notes), 1)
        self.assertIn("东京-固定A", self.sys.notes[0][1])
        with open(self.paths.history, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("203.0.113.10(东京-固定A) → 198.51.100.22(新加坡-固定B)", lines[1])

    def test_unknown_exit_dedup_10min(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "8.8.8.8"}), "ipinfo.io": IPINFO.replace("203.0.113.10", "8.8.8.8")})
        for _ in range(5):          # 5 分钟内刷新 5 次
            snap = self.run_once(http, force=True)
            self.t[0] += 60
        self.assertEqual(snap["status"]["level"], core.YELLOW)
        self.assertEqual(len(self.sys.notes), 1)
        self.assertTrue(snap["notifications"][0]["suppressed"])
        self.t[0] += 600
        self.run_once(http, force=True)
        self.assertEqual(len(self.sys.notes), 2)

    def test_suspected_direct_and_proxy_off_signal(self):
        self.sys.qx, self.sys.tuns = False, []
        http = FakeHttp({"ipify": json.dumps({"ip": "192.0.2.50"}), "ipinfo.io": IPINFO.replace("203.0.113.10", "192.0.2.50")})
        snap = self.run_once(http)
        self.assertEqual(snap["status"]["reason"], core.REASON_DIRECT)
        self.assertTrue(snap["signals"]["proxy_likely_off"])
        self.assertEqual(self.sys.notes[0][0], "⚠️ 疑似直连")

    def test_offline_then_recover(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        self.run_once(http)
        self.t[0] += 61
        snap = self.run_once(FakeHttp({}))
        self.assertEqual(snap["status"]["reason"], core.REASON_OFFLINE)
        self.assertEqual(len(snap["source"]["attempts"]), 2 + 3)
        self.t[0] += 10   # 离线时不等 60 秒，每次都重试
        snap = self.run_once(http)
        self.assertTrue(snap["source"]["refreshed"])
        self.assertEqual(snap["status"]["level"], core.GREEN)
        self.assertEqual(self.sys.notes, [])   # 掉线又回到同一个 IP：不算“切换”
        with open(self.paths.history, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertIn("→ 离线", lines[1])
        self.assertIn("离线 → 203.0.113.10", lines[2])

    def test_ip_only_fails_geo_self_lookup(self):
        http = FakeHttp({"ipify": http_429("x"), "checkip": http_429("x"), "ipwho.is": IPWHO})
        snap = self.run_once(http)
        self.assertEqual(snap["ip"], "203.0.113.10")
        self.assertEqual(snap["source"]["ip_provider"], "ipwho.is")

    def test_geo_unavailable_uses_expect_tz(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"})})
        snap = self.run_once(http)
        self.assertEqual(snap["status"]["level"], core.GREEN)
        self.assertEqual(snap["timezones"]["exit"]["tz"], "Asia/Tokyo")
        self.assertEqual(snap["timezones"]["exit_source"], "expect_tz")

    def test_system_tz_unknown_falls_back_to_offset(self):
        self.sys.system_timezone = lambda: None
        self.sys.local_offset_seconds = lambda: 3600
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        snap = self.run_once(http)
        self.assertEqual(snap["timezones"]["system"]["offset"], "UTC+1")
        self.assertTrue(snap["timezones"]["mismatch"])

    def test_iface_change_resamples(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        self.run_once(http)
        self.sys.names = ["en0", "en5"]
        self.t[0] += 10
        self.run_once(http)
        self.assertEqual(self.slept, [1.0, 1.0])

    def test_history_capped(self):
        for i in range(210):
            app.append_history(self.paths.history, "line %d" % i)
        with open(self.paths.history, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual((len(lines), lines[0]), (200, "line 10"))

    def test_render_swiftbar(self):
        http = FakeHttp({"ipify": json.dumps({"ip": "203.0.113.10"}), "ipwho.is": IPWHO})
        snap = self.run_once(http)
        actions = {a: ["/usr/bin/python3", "/p/netwidget.10s.py", "--action", a] for a in cli.ACTIONS}
        out = core.render_swiftbar(snap, actions)
        first = out.splitlines()[0]
        self.assertEqual(first, "🟢 🇯🇵")
        self.assertEqual(out.splitlines()[1], "---")
        self.assertIn("Quantumult X 进程：运行中", out)
        self.assertIn("utun4 198.18.0.1", out)
        self.assertIn('param3="copy-ip" terminal=false', out)
        self.assertIn('param3="refresh" terminal=false refresh=true', out)
        for line in out.splitlines():
            self.assertLessEqual(line.count("|"), 1, line)
        # 出口与本机时区不同是正常的，不再提示；出口与白名单预期一致时给出确认
        self.assertNotIn("系统时区不一致", out)
        self.assertIn("✅ 出口 IP 与时区都和代理一致（Asia/Tokyo）", out)

    def test_render_offline(self):
        snap = self.run_once(FakeHttp({}))
        out = core.render_swiftbar(snap)
        self.assertTrue(out.startswith("🔴 离线"))
        self.assertNotIn("复制出口 IP", out)


class ConfigTest(unittest.TestCase):
    def test_validate_skips_bad_entries(self):
        cfg, errors = validate({"exits": [{"ip": "203.0.113.10", "label": "A", "expect_tz": "Asia/Tokyo"},
                                          {"ip": "nope"}, {"label": "no ip"},
                                          {"ip": "198.51.100.1", "expect_tz": "Bad/Zone"}],
                                "real_ip_hints": ["192.0.2.0/24", "x/99"], "refresh_ip_seconds": 1})
        self.assertEqual([e["ip"] for e in cfg["exits"]], ["203.0.113.10", "198.51.100.1"])
        self.assertIsNone(cfg["exits"][1]["expect_tz"])
        self.assertEqual(cfg["real_ip_hints"], ["192.0.2.0/24"])
        self.assertEqual(cfg["refresh_ip_seconds"], 15)
        self.assertEqual(len(errors), 4)

    def test_example_config_is_valid_and_uses_documentation_ips(self):
        import ipaddress
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "config.example.json"), encoding="utf-8") as f:
            raw = json.load(f)
        cfg, errors = validate(raw)
        self.assertEqual(errors, [])
        doc_nets = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
        for item in [e["ip"] for e in cfg["exits"]] + cfg["real_ip_hints"]:
            net = ipaddress.ip_network(item, strict=False)
            self.assertTrue(any(net.subnet_of(d) for d in doc_nets), item)


if __name__ == "__main__":
    unittest.main()
