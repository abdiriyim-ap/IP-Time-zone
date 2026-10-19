"""和操作系统打交道的薄封装（子进程 / 文件）。逻辑都在 core.py。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import core

IS_MAC = sys.platform == "darwin"


def _bin(name: str, *candidates: str) -> str:
    """SwiftBar 的 PATH 可能很短，优先用系统绝对路径。"""
    for c in candidates:
        if os.path.exists(c):
            return c
    return shutil.which(name) or name


def run(cmd: Sequence[str], timeout: float = 3.0, input_text: Optional[str] = None) -> Tuple[int, str]:
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, input=input_text)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def read_counters() -> Dict[str, Tuple[int, int]]:
    if IS_MAC:
        code, out = run([_bin("netstat", "/usr/sbin/netstat"), "-ib"])
        return core.parse_netstat_ib(out) if code == 0 else {}
    try:
        with open("/proc/net/dev", "r") as f:
            return core.parse_proc_net_dev(f.read())
    except OSError:
        return {}


def sample_counters() -> Dict[str, object]:
    counters = read_counters()
    rx, tx, used = core.sum_physical(counters)
    names = sorted(n for n in counters if core.is_physical_iface(n))
    return {"ts": time.time(), "rx": rx, "tx": tx, "ifaces": used, "names": names}


def system_timezone() -> Optional[str]:
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz and core.get_zone(tz) is not None:
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    marker = "zoneinfo/"
    if marker in target:
        name = target.split(marker, 1)[1]
        if core.get_zone(name) is not None:
            return name
    if os.path.exists("/etc/timezone"):
        try:
            with open("/etc/timezone") as f:
                name = f.read().strip()
            if core.get_zone(name) is not None:
                return name
        except OSError:
            pass
    return None


def quantumult_x_running() -> Optional[bool]:
    if not IS_MAC:
        return None
    code, _ = run([_bin("pgrep", "/usr/bin/pgrep"), "-if", "quantumult"])
    if code == 127:
        return None
    return code == 0


def tun_ipv4() -> List[Dict[str, str]]:
    if not IS_MAC:
        return []
    code, out = run([_bin("ifconfig", "/sbin/ifconfig")])
    return core.parse_ifconfig_tun(out) if code == 0 else []


def notify(title: str, message: str) -> bool:
    if not IS_MAC:
        return False
    script = ["-e", "on run argv", "-e",
              "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]
    code, _ = run([_bin("osascript", "/usr/bin/osascript")] + script + [title, message], timeout=5)
    return code == 0


def copy_to_clipboard(text: str) -> bool:
    if not IS_MAC:
        print(text)
        return False
    code, _ = run([_bin("pbcopy", "/usr/bin/pbcopy")], input_text=text)
    return code == 0


def open_text(path: str) -> bool:
    if not IS_MAC:
        print(path)
        return False
    code, _ = run([_bin("open", "/usr/bin/open"), "-t", path])
    return code == 0
