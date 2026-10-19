"""纯函数层：不做任何网络 / 文件 / 子进程 I/O，方便单元测试。"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

GREEN = "green"
YELLOW = "yellow"
RED = "red"

DOTS = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴"}

REASON_WHITELISTED = "whitelisted"
REASON_UNKNOWN = "unknown_exit"
REASON_DIRECT = "suspected_direct"
REASON_OFFLINE = "offline"

REASON_TEXT = {
    REASON_WHITELISTED: "出口在白名单内",
    REASON_UNKNOWN: "未知出口（不在白名单）",
    REASON_DIRECT: "疑似直连 / 代理掉线（命中 real_ip_hints）",
    REASON_OFFLINE: "无法获取出口 IP（离线或所有接口失败）",
}


# ---------------------------------------------------------------- IP 匹配

def normalize_ip(value: str) -> Optional[str]:
    """返回规范化的 IP 字符串；非法输入返回 None。"""
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return None


def _ip_matches(ip_obj, pattern: str) -> bool:
    pattern = str(pattern).strip()
    try:
        if "/" in pattern:
            return ip_obj in ipaddress.ip_network(pattern, strict=False)
        return ip_obj == ipaddress.ip_address(pattern)
    except (ValueError, TypeError):
        return False


def match_exit(ip: Optional[str], exits: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """在白名单中查找出口。exits[].ip 可以是单个 IP，也可以是 CIDR。"""
    if not ip:
        return None
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for entry in exits or []:
        if isinstance(entry, dict) and entry.get("ip") and _ip_matches(ip_obj, entry["ip"]):
            return entry
    return None


def match_hints(ip: Optional[str], hints: Iterable[str]) -> Optional[str]:
    """如果 ip 落在 real_ip_hints 的任一网段，返回该网段字符串。"""
    if not ip:
        return None
    try:
        ip_obj = ipaddress.ip_address(ip)
    except ValueError:
        return None
    for hint in hints or []:
        if _ip_matches(ip_obj, hint):
            return str(hint)
    return None


# ---------------------------------------------------------------- 状态判断

def compute_status(
    ip: Optional[str],
    exits: Sequence[Dict[str, Any]],
    hints: Sequence[str],
    geo: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """根据出口 IP + 白名单 + 直连网段算出状态灯。

    优先级：离线 > 白名单命中（显式配置最具体）> real_ip_hints 命中 > 未知。
    """
    geo = geo or {}
    warnings: List[str] = []
    if not ip:
        return {"level": RED, "reason": REASON_OFFLINE, "label": None,
                "exit": None, "hint": None, "warnings": warnings}

    entry = match_exit(ip, exits)
    if entry is not None:
        expect_tz = entry.get("expect_tz")
        geo_tz = geo.get("timezone")
        if expect_tz and geo_tz and not same_utc_offset(expect_tz, geo_tz, now):
            warnings.append("出口地理时区 %s 与白名单预期 %s 不一致" % (geo_tz, expect_tz))
        return {"level": GREEN, "reason": REASON_WHITELISTED, "label": entry.get("label") or ip,
                "exit": entry, "hint": None, "warnings": warnings}

    hint = match_hints(ip, hints)
    if hint is not None:
        return {"level": RED, "reason": REASON_DIRECT, "label": None,
                "exit": None, "hint": hint, "warnings": warnings}

    if not exits:
        warnings.append("还没有配置固定 IP 白名单")
    return {"level": YELLOW, "reason": REASON_UNKNOWN, "label": None,
            "exit": None, "hint": None, "warnings": warnings}


# ---------------------------------------------------------------- 显示格式

def flag_emoji(country_code: Optional[str]) -> str:
    cc = (country_code or "").strip().upper()
    if len(cc) != 2 or not cc.isalpha():
        return "🌐"
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


def format_rate(bytes_per_sec: Optional[float], compact: bool = False) -> str:
    """compact=True 用于菜单栏（2.3M），否则用于面板（2.3 MB/s）。"""
    if bytes_per_sec is None:
        return "—"
    value = max(0.0, float(bytes_per_sec))
    units = ["B", "K", "M", "G"]
    idx = 0
    while value >= 1000 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        num = "%d" % round(value)
    elif value >= 100:
        num = "%d" % round(value)
    else:
        num = "%.1f" % value
    if compact:
        return num + units[idx]
    return "%s %s/s" % (num, units[idx] + ("B" if idx else ""))


def format_offset(delta: Optional[timedelta]) -> str:
    if delta is None:
        return "UTC?"
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if minutes:
        return "UTC%s%d:%02d" % (sign, hours, minutes)
    return "UTC%s%d" % (sign, hours)


def get_zone(name: Optional[str]):
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def describe_tz(name: Optional[str], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """返回 {tz, time, date, offset, offset_seconds}；时区无效返回 None。"""
    zone = get_zone(name)
    if zone is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(zone)
    off = local.utcoffset()
    return {
        "tz": name,
        "time": local.strftime("%H:%M"),
        "date": local.strftime("%Y-%m-%d"),
        "offset": format_offset(off),
        "offset_seconds": int(off.total_seconds()) if off is not None else None,
    }


def describe_fixed_offset(offset_seconds: int, label: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """系统时区名拿不到时的兜底：只按 UTC 偏移显示。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = timedelta(seconds=int(offset_seconds))
    local = now.astimezone(timezone(delta))
    return {"tz": label, "time": local.strftime("%H:%M"), "date": local.strftime("%Y-%m-%d"),
            "offset": format_offset(delta), "offset_seconds": int(offset_seconds)}


def same_utc_offset(tz_a: Optional[str], tz_b: Optional[str], now: Optional[datetime] = None) -> bool:
    """两个时区此刻 UTC 偏移是否相同（Asia/Shanghai 与 Asia/Chongqing 视为相同）。

    任一无效时返回 True（无法判断就不报警）。
    """
    a, b = describe_tz(tz_a, now), describe_tz(tz_b, now)
    if a is None or b is None:
        return True
    return a["offset_seconds"] == b["offset_seconds"]


# ---------------------------------------------------------------- 网速

_SKIP_PREFIXES = ("lo", "utun", "gif", "stf", "awdl", "llw", "bridge", "ap", "anpi",
                  "ipsec", "ppp", "tun", "tap", "docker", "veth", "br-", "virbr", "vmnet")
_PHYSICAL_PREFIXES = ("en", "eth", "wl", "wlan")


def is_physical_iface(name: str) -> bool:
    """只统计物理网卡，避免 TUN（utun*）与物理网卡重复计数。"""
    n = name.rstrip("*")
    if n.startswith(_SKIP_PREFIXES):
        return False
    return n.startswith(_PHYSICAL_PREFIXES)


def parse_netstat_ib(text: str) -> Dict[str, Tuple[int, int]]:
    """解析 macOS `netstat -ib` 输出 → {iface: (rx_bytes, tx_bytes)}。

    每个接口只取 <Link#N> 那一行（链路层计数）。Address 列可能为空，
    所以按表头从右往左定位 Ibytes / Obytes。
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    header = lines[0].split()
    try:
        n = len(header)
        ib_from_end = header.index("Ibytes") - n
        ob_from_end = header.index("Obytes") - n
    except ValueError:
        return {}
    result: Dict[str, Tuple[int, int]] = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4 or not parts[2].startswith("<Link#"):
            continue
        if len(parts) < n - 1:
            continue
        name = parts[0].rstrip("*")
        try:
            rx = int(parts[ib_from_end])
            tx = int(parts[ob_from_end])
        except (ValueError, IndexError):
            continue
        if name in result:
            continue
        result[name] = (rx, tx)
    return result


def parse_proc_net_dev(text: str) -> Dict[str, Tuple[int, int]]:
    """解析 Linux /proc/net/dev（仅用于 CI / 开发机）。"""
    result: Dict[str, Tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, data = line.split(":", 1)
        fields = data.split()
        if len(fields) < 9:
            continue
        try:
            result[name.strip()] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return result


def sum_physical(counters: Dict[str, Tuple[int, int]]) -> Tuple[int, int, List[str]]:
    rx = tx = 0
    used: List[str] = []
    for name in sorted(counters):
        if is_physical_iface(name):
            r, t = counters[name]
            rx += r
            tx += t
            if r or t:
                used.append(name)
    return rx, tx, used


def compute_speed(prev: Optional[Dict[str, Any]], cur: Dict[str, Any],
                  max_age: float = 180.0) -> Optional[Tuple[float, float]]:
    """prev/cur = {"ts": 秒, "rx": 字节, "tx": 字节, "ifaces": [...]} → (down B/s, up B/s)。

    计数回绕/重置、网卡集合变化、间隔过长或过短时返回 None（显示 “—”）。
    """
    if not prev or not cur:
        return None
    try:
        dt = float(cur["ts"]) - float(prev["ts"])
        drx = int(cur["rx"]) - int(prev["rx"])
        dtx = int(cur["tx"]) - int(prev["tx"])
    except (KeyError, TypeError, ValueError):
        return None
    if dt <= 0.2 or dt > max_age:
        return None
    if drx < 0 or dtx < 0:
        return None
    return drx / dt, dtx / dt


# ---------------------------------------------------------------- ifconfig（TUN 检测）

def parse_ifconfig_tun(text: str) -> List[Dict[str, str]]:
    """从 `ifconfig` 输出中找出带 IPv4 地址的 utun 接口。

    macOS 默认就有 utun0~utun3（iCloud 等系统服务，只有 IPv6 链路本地地址），
    所以“存在 utun”不代表代理开着；带 IPv4 的 utun（如 QX 的 198.18.0.1）才算。
    """
    found: List[Dict[str, str]] = []
    current = None
    for line in text.splitlines():
        if line and not line[0].isspace() and ":" in line:
            current = line.split(":", 1)[0]
            continue
        s = line.strip()
        if current and current.startswith("utun") and s.startswith("inet "):
            parts = s.split()
            if len(parts) >= 2:
                found.append({"iface": current, "ipv4": parts[1]})
    return found


# ---------------------------------------------------------------- 通知与历史

def notification_events(prev_online_ip: Optional[str], prev_label: Optional[str],
                        ip: Optional[str], status: Dict[str, Any],
                        geo: Optional[Dict[str, Any]] = None) -> List[Tuple[str, str, str]]:
    """返回 [(去重 key, 标题, 正文)]。离线本身不弹通知（断网时通知也没意义）。"""
    geo = geo or {}
    events: List[Tuple[str, str, str]] = []
    if not ip:
        return events
    reason = status.get("reason")
    where = geo.get("country") or geo.get("country_code") or "未知地区"
    if reason == REASON_DIRECT:
        events.append(("direct:%s" % ip, "⚠️ 疑似直连",
                       "代理可能已断开，当前出口 %s 落在本地网段 %s" % (ip, status.get("hint"))))
    elif reason == REASON_UNKNOWN:
        events.append(("unknown:%s" % ip, "🟡 未知出口",
                       "当前出口 %s（%s）不在你的固定 IP 列表" % (ip, where)))
    if prev_online_ip and prev_online_ip != ip and reason == REASON_WHITELISTED:
        old = prev_label or prev_online_ip
        events.append(("change:%s->%s" % (prev_online_ip, ip), "出口已切换",
                       "出口已从 %s 切换到 %s" % (old, status.get("label") or ip)))
    return events


def should_notify(key: str, sent: Dict[str, float], now_ts: float, window: float = 600.0) -> bool:
    last = sent.get(key)
    return last is None or now_ts - float(last) >= window


def prune_sent(sent: Dict[str, float], now_ts: float, window: float = 600.0) -> Dict[str, float]:
    return {k: v for k, v in sent.items() if now_ts - float(v) < window * 6}


def describe_ip(ip: Optional[str], label: Optional[str]) -> str:
    if not ip:
        return label or "离线"
    return "%s(%s)" % (ip, label) if label else ip


def history_line(when: datetime, old_ip: Optional[str], old_label: Optional[str],
                 new_ip: Optional[str], new_label: Optional[str], level: str,
                 country: Optional[str] = None) -> str:
    stamp = when.strftime("%Y-%m-%d %H:%M:%S %z")
    extra = " %s" % country if country else ""
    return "%s  %s %s → %s%s" % (stamp, DOTS.get(level, "?"), describe_ip(old_ip, old_label),
                                 describe_ip(new_ip, new_label), extra)


def trim_lines(lines: List[str], keep: int = 200) -> List[str]:
    lines = [ln for ln in lines if ln.strip()]
    return lines[-keep:]


# ---------------------------------------------------------------- SwiftBar 渲染

def _clean(text: Any) -> str:
    """SwiftBar 用 `|` 分隔参数，文本里不能出现。"""
    return str(text).replace("|", "/").replace("\n", " ").strip()


def _q(value: str) -> str:
    return '"%s"' % str(value).replace('"', '\\"')


def action_params(cmd: Sequence[str], refresh: bool = False) -> str:
    parts = ["bash=%s" % _q(cmd[0])]
    for i, arg in enumerate(cmd[1:], start=1):
        parts.append("param%d=%s" % (i, _q(arg)))
    parts.append("terminal=false")
    if refresh:
        parts.append("refresh=true")
    return " ".join(parts)


def title_line(snapshot: Dict[str, Any]) -> str:
    status = snapshot["status"]
    geo = snapshot.get("geo") or {}
    dot = DOTS[status["level"]]
    if status["reason"] == REASON_OFFLINE:
        name, flag = "离线", "🌐"
    else:
        flag = flag_emoji(geo.get("country_code"))
        name = status.get("label") or geo.get("country_code") or snapshot.get("ip") or "?"
        if status["reason"] == REASON_DIRECT:
            name = "直连?"
    speed = snapshot.get("speed") or {}
    down = format_rate(speed.get("down_bps"), compact=True)
    up = format_rate(speed.get("up_bps"), compact=True)
    return "%s %s %s ↓%s ↑%s" % (dot, flag, _clean(name), down, up)


def render_swiftbar(snapshot: Dict[str, Any], actions: Optional[Dict[str, Sequence[str]]] = None) -> str:
    actions = actions or {}
    status = snapshot["status"]
    geo = snapshot.get("geo") or {}
    ip = snapshot.get("ip")
    color = {GREEN: "#2e7d32", YELLOW: "#b8860b", RED: "#c62828"}[status["level"]]
    warn = "#e65100"
    mono = "font=Menlo size=12"
    out: List[str] = [title_line(snapshot), "---"]

    out.append("出口（当前流量从这里出去） | size=12 color=#888888")
    if ip:
        mark = "✅ " + _clean(status["label"]) if status["reason"] == REASON_WHITELISTED else ""
        out.append(("%s  %s" % (_clean(ip), mark)).strip() + " | " + mono)
        loc = " · ".join(_clean(x) for x in (geo.get("country"), geo.get("region"), geo.get("city")) if x)
        net = " · ".join(_clean(x) for x in (geo.get("isp") or geo.get("org"), geo.get("asn")) if x)
        if loc:
            out.append("%s %s" % (flag_emoji(geo.get("country_code")), loc))
        if net:
            out.append(net + " | size=12")
        if not loc and not net:
            out.append("（地理信息暂不可用） | color=#888888")
    out.append("%s %s | color=%s" % (DOTS[status["level"]], REASON_TEXT[status["reason"]], color))
    if status.get("hint"):
        out.append("命中直连网段 %s | color=%s" % (_clean(status["hint"]), color))
    for w in status.get("warnings") or []:
        out.append("⚠️ %s | color=%s" % (_clean(w), warn))
    for err in (snapshot.get("config") or {}).get("errors") or []:
        out.append("⚠️ 配置：%s | color=%s" % (_clean(err), warn))

    out.append("---")
    out.append("时区 | size=12 color=#888888")
    tzinfo = snapshot.get("timezones") or {}
    exit_tz, sys_tz = tzinfo.get("exit"), tzinfo.get("system")
    if exit_tz:
        src = "（白名单预期）" if tzinfo.get("exit_source") == "expect_tz" else ""
        out.append("出口  %s  %s %s  %s%s | %s" % (exit_tz["tz"], exit_tz["date"][5:], exit_tz["time"],
                                                 exit_tz["offset"], src, mono))
    else:
        out.append("出口  未知 | %s" % mono)
    if sys_tz:
        out.append("本机  %s  %s %s  %s | %s" % (sys_tz["tz"], sys_tz["date"][5:], sys_tz["time"],
                                              sys_tz["offset"], mono))
    if tzinfo.get("mismatch"):
        out.append("⚠️ 出口时区与系统时区不一致（出差常见，仅提示） | color=%s" % warn)

    out.append("---")
    speed = snapshot.get("speed") or {}
    ifaces = ", ".join(speed.get("ifaces") or []) or "无"
    out.append("网速（物理网卡：%s） | size=12 color=#888888" % _clean(ifaces))
    out.append("↓ %s    ↑ %s | %s" % (format_rate(speed.get("down_bps")), format_rate(speed.get("up_bps")), mono))

    signals = snapshot.get("signals") or {}
    if signals:
        out.append("---")
        out.append("辅助信号（仅供参考） | size=12 color=#888888")
        qx = signals.get("quantumult_x_running")
        out.append("Quantumult X 进程：%s" % ({True: "运行中", False: "未运行"}.get(qx, "未知")))
        tuns = signals.get("tun_ipv4") or []
        if tuns:
            out.append("TUN 网卡：%s" % _clean(", ".join("%s %s" % (t["iface"], t["ipv4"]) for t in tuns)))
        else:
            out.append("TUN 网卡：没有带 IPv4 的 utun")
        if signals.get("proxy_likely_off"):
            out.append("⚠️ QX 未运行且无 TUN：代理很可能没开 | color=#c62828")

    out.append("---")
    src = snapshot.get("source") or {}
    age = src.get("age_seconds")
    age_txt = ("%d 秒前" % age) if isinstance(age, (int, float)) else "刚刚"
    out.append("来源：%s · 地理 %s · %s | size=11 color=#888888" % (
        _clean(src.get("ip_provider") or "—"), _clean(src.get("geo_provider") or "—"), age_txt))
    labels = [("refresh", "立即刷新", True), ("copy-ip", "复制出口 IP", False),
              ("open-config", "编辑白名单…", False), ("open-history", "查看变化记录…", False)]
    for key, text, refresh in labels:
        if key == "copy-ip" and not ip:
            continue
        cmd = actions.get(key)
        if cmd:
            out.append("%s | %s" % (text, action_params(cmd, refresh=refresh)))
        elif key == "refresh":
            out.append("%s | refresh=true" % text)
    return "\n".join(out) + "\n"
