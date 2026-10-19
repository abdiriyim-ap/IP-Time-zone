import os
import unittest
from datetime import datetime, timezone

from netwidget import core

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


EXITS = [
    {"ip": "203.0.113.10", "label": "东京-固定A", "expect_tz": "Asia/Tokyo"},
    {"ip": "198.51.100.22", "label": "新加坡-固定B", "expect_tz": "Asia/Singapore"},
    {"ip": "203.0.113.64/29", "label": "洛杉矶-固定段", "expect_tz": "America/Los_Angeles"},
    {"ip": "2001:db8::10", "label": "IPv6-固定", "expect_tz": None},
]
HINTS = ["192.0.2.0/24", "2001:db8:ffff::/48"]
NOW = datetime(2026, 1, 15, 4, 0, tzinfo=timezone.utc)  # 冬季，无夏令时干扰


class WhitelistTest(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(core.match_exit("203.0.113.10", EXITS)["label"], "东京-固定A")

    def test_no_match(self):
        self.assertIsNone(core.match_exit("203.0.113.11", EXITS))

    def test_cidr_entry(self):
        self.assertEqual(core.match_exit("203.0.113.70", EXITS)["label"], "洛杉矶-固定段")
        self.assertIsNone(core.match_exit("203.0.113.72", EXITS))

    def test_ipv6_normalized(self):
        self.assertEqual(core.match_exit("2001:0db8:0000::0010", EXITS)["label"], "IPv6-固定")

    def test_garbage(self):
        self.assertIsNone(core.match_exit("not-an-ip", EXITS))
        self.assertIsNone(core.match_exit(None, EXITS))
        self.assertIsNone(core.match_exit("203.0.113.10", [{"ip": "bad"}, {"label": "x"}, "junk"]))


class HintsTest(unittest.TestCase):
    def test_cidr_hit(self):
        self.assertEqual(core.match_hints("192.0.2.77", HINTS), "192.0.2.0/24")

    def test_cidr_miss(self):
        self.assertIsNone(core.match_hints("192.0.3.1", HINTS))

    def test_ipv6_hint_and_family_mismatch(self):
        self.assertEqual(core.match_hints("2001:db8:ffff:1::5", HINTS), "2001:db8:ffff::/48")
        self.assertIsNone(core.match_hints("10.0.0.1", ["2001:db8::/32"]))

    def test_single_ip_hint(self):
        self.assertEqual(core.match_hints("192.0.2.1", ["192.0.2.1"]), "192.0.2.1")


class StatusTest(unittest.TestCase):
    def test_green(self):
        s = core.compute_status("203.0.113.10", EXITS, HINTS, {"timezone": "Asia/Tokyo"}, NOW)
        self.assertEqual((s["level"], s["reason"], s["label"]), (core.GREEN, core.REASON_WHITELISTED, "东京-固定A"))
        self.assertEqual(s["warnings"], [])

    def test_green_with_tz_warning(self):
        s = core.compute_status("203.0.113.10", EXITS, HINTS, {"timezone": "Europe/London"}, NOW)
        self.assertEqual(s["level"], core.GREEN)
        self.assertEqual(len(s["warnings"]), 1)

    def test_same_offset_different_name_no_warning(self):
        s = core.compute_status("203.0.113.10", EXITS, HINTS, {"timezone": "Asia/Seoul"}, NOW)
        self.assertEqual(s["warnings"], [])  # 首尔与东京同为 UTC+9

    def test_yellow(self):
        s = core.compute_status("8.8.8.8", EXITS, HINTS, {}, NOW)
        self.assertEqual((s["level"], s["reason"]), (core.YELLOW, core.REASON_UNKNOWN))

    def test_yellow_no_whitelist_warns(self):
        s = core.compute_status("8.8.8.8", [], [], {}, NOW)
        self.assertEqual(s["level"], core.YELLOW)
        self.assertTrue(s["warnings"])

    def test_red_direct(self):
        s = core.compute_status("192.0.2.9", EXITS, HINTS, {}, NOW)
        self.assertEqual((s["level"], s["reason"], s["hint"]), (core.RED, core.REASON_DIRECT, "192.0.2.0/24"))

    def test_red_offline(self):
        s = core.compute_status(None, EXITS, HINTS, {}, NOW)
        self.assertEqual((s["level"], s["reason"]), (core.RED, core.REASON_OFFLINE))

    def test_whitelist_beats_hint(self):
        s = core.compute_status("192.0.2.5", [{"ip": "192.0.2.5", "label": "家里固定"}], HINTS, {}, NOW)
        self.assertEqual(s["level"], core.GREEN)


class SpeedTest(unittest.TestCase):
    def test_parse_netstat_link_rows_only(self):
        c = core.parse_netstat_ib(fixture("netstat_ib_t0.txt"))
        self.assertEqual(c["en0"], (12000000000, 1500000000))
        self.assertEqual(c["utun0"], (0, 1200))  # 没有 Address 列也能解析
        self.assertEqual(c["gif0"], (0, 0))      # 去掉了 * 后缀
        self.assertEqual(c["utun4"], (11000000000, 1400000000))
        self.assertEqual(c["lo0"], (254000000, 254000000))

    def test_physical_filter(self):
        for name in ("en0", "en5", "eth0", "wlan0", "wlp2s0", "enp0s4"):
            self.assertTrue(core.is_physical_iface(name), name)
        for name in ("lo0", "utun4", "bridge0", "awdl0", "llw0", "anpi0", "gif0", "stf0", "docker0", "veth1"):
            self.assertFalse(core.is_physical_iface(name), name)

    def test_sum_physical_excludes_utun(self):
        rx, tx, used = core.sum_physical(core.parse_netstat_ib(fixture("netstat_ib_t0.txt")))
        self.assertEqual(rx, 12000000000 + 60000000)
        self.assertEqual(tx, 1500000000 + 5000000)
        self.assertEqual(used, ["en0", "en5"])

    def test_speed_delta_from_fixtures(self):
        a = core.sum_physical(core.parse_netstat_ib(fixture("netstat_ib_t0.txt")))
        b = core.sum_physical(core.parse_netstat_ib(fixture("netstat_ib_t1.txt")))
        down, up = core.compute_speed({"ts": 100.0, "rx": a[0], "tx": a[1]},
                                      {"ts": 110.0, "rx": b[0], "tx": b[1]})
        self.assertAlmostEqual(down, (23552000 + 1024000) / 10.0)
        self.assertAlmostEqual(up, 1843200 / 10.0)

    def test_speed_invalid_cases(self):
        cur = {"ts": 110.0, "rx": 100, "tx": 100}
        self.assertIsNone(core.compute_speed(None, cur))
        self.assertIsNone(core.compute_speed({"ts": 110.0, "rx": 0, "tx": 0}, cur))      # dt=0
        self.assertIsNone(core.compute_speed({"ts": 100.0, "rx": 500, "tx": 0}, cur))    # 计数回绕
        self.assertIsNone(core.compute_speed({"ts": -500.0, "rx": 0, "tx": 0}, cur))     # 太久
        self.assertIsNone(core.compute_speed({"ts": "x"}, cur))

    def test_parse_proc_net_dev(self):
        c = core.parse_proc_net_dev(fixture("proc_net_dev.txt"))
        self.assertEqual(c["eth0"], (98765432, 12345678))
        self.assertEqual(core.sum_physical(c)[:2], (98765432, 12345678))

    def test_parse_netstat_garbage(self):
        self.assertEqual(core.parse_netstat_ib(""), {})
        self.assertEqual(core.parse_netstat_ib("no header here\nen0 1 2 3"), {})

    def test_format_rate(self):
        self.assertEqual(core.format_rate(None), "—")
        self.assertEqual(core.format_rate(0, compact=True), "0B")
        self.assertEqual(core.format_rate(999, compact=True), "999B")
        self.assertEqual(core.format_rate(180 * 1024, compact=True), "180K")
        self.assertEqual(core.format_rate(2.3 * 1024 * 1024, compact=True), "2.3M")
        self.assertEqual(core.format_rate(2.3 * 1024 * 1024), "2.3 MB/s")
        self.assertEqual(core.format_rate(512), "512 B/s")


class TimezoneTest(unittest.TestCase):
    def test_describe_tz(self):
        d = core.describe_tz("Asia/Tokyo", NOW)
        self.assertEqual((d["time"], d["date"], d["offset"], d["offset_seconds"]),
                         ("13:00", "2026-01-15", "UTC+9", 32400))

    def test_half_hour_and_negative(self):
        self.assertEqual(core.describe_tz("Asia/Kolkata", NOW)["offset"], "UTC+5:30")
        self.assertEqual(core.describe_tz("America/New_York", NOW)["offset"], "UTC-5")
        self.assertEqual(core.describe_tz("America/New_York", NOW)["date"], "2026-01-14")

    def test_dst(self):
        summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(core.describe_tz("America/Los_Angeles", summer)["offset"], "UTC-7")

    def test_invalid(self):
        self.assertIsNone(core.describe_tz("Mars/Olympus", NOW))
        self.assertIsNone(core.describe_tz(None, NOW))
        self.assertTrue(core.same_utc_offset("Mars/Olympus", "Asia/Tokyo", NOW))

    def test_same_offset(self):
        self.assertTrue(core.same_utc_offset("Asia/Shanghai", "Asia/Singapore", NOW))
        self.assertFalse(core.same_utc_offset("Asia/Shanghai", "Asia/Tokyo", NOW))

    def test_fixed_offset_fallback(self):
        d = core.describe_fixed_offset(8 * 3600, "本地", NOW)
        self.assertEqual((d["time"], d["offset"], d["offset_seconds"]), ("12:00", "UTC+8", 28800))

    def test_naive_now_is_utc(self):
        self.assertEqual(core.describe_tz("Asia/Tokyo", NOW.replace(tzinfo=None))["time"], "13:00")


class MiscTest(unittest.TestCase):
    def test_flag(self):
        self.assertEqual(core.flag_emoji("jp"), "🇯🇵")
        self.assertEqual(core.flag_emoji(None), "🌐")
        self.assertEqual(core.flag_emoji("XYZ"), "🌐")

    def test_tun_parse(self):
        self.assertEqual(core.parse_ifconfig_tun(fixture("ifconfig_macos.txt")),
                         [{"iface": "utun4", "ipv4": "198.18.0.1"}])

    def test_notification_events(self):
        green = core.compute_status("198.51.100.22", EXITS, HINTS, {}, NOW)
        ev = core.notification_events("203.0.113.10", "东京-固定A", "198.51.100.22", green)
        self.assertEqual(len(ev), 1)
        self.assertIn("东京-固定A", ev[0][2])
        self.assertIn("新加坡-固定B", ev[0][2])
        unknown = core.compute_status("8.8.8.8", EXITS, HINTS, {}, NOW)
        ev = core.notification_events("203.0.113.10", None, "8.8.8.8", unknown, {"country": "美国"})
        self.assertEqual([e[0] for e in ev], ["unknown:8.8.8.8"])
        direct = core.compute_status("192.0.2.9", EXITS, HINTS, {}, NOW)
        self.assertEqual(core.notification_events(None, None, "192.0.2.9", direct)[0][0], "direct:192.0.2.9")
        self.assertEqual(core.notification_events("203.0.113.10", None, None,
                                                  core.compute_status(None, EXITS, HINTS)), [])
        self.assertEqual(core.notification_events("203.0.113.10", None, "203.0.113.10",
                                                  core.compute_status("203.0.113.10", EXITS, HINTS)), [])

    def test_dedup_window(self):
        sent = {"k": 1000.0}
        self.assertFalse(core.should_notify("k", sent, 1000.0 + 599))
        self.assertTrue(core.should_notify("k", sent, 1000.0 + 600))
        self.assertTrue(core.should_notify("other", sent, 1000.0))

    def test_history(self):
        when = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        line = core.history_line(when, "203.0.113.10", "东京-固定A", None, None, core.RED)
        self.assertIn("203.0.113.10(东京-固定A) → 离线", line)
        self.assertEqual(core.trim_lines([str(i) for i in range(250)], 200)[0], "50")
