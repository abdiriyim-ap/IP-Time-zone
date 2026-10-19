"""命令行入口。SwiftBar 直接运行插件（无参数）→ 输出菜单；CI 用 --once --json。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import List, Optional

from . import app, core
from . import system as sysmod
from .config import example_config_path

ACTIONS = ("refresh", "copy-ip", "open-config", "open-history")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="netwidget", description="出口 IP / 时区 / 网速 菜单栏小组件")
    p.add_argument("--once", action="store_true", help="只运行一次（默认行为，供 CI 明确表达）")
    p.add_argument("--json", action="store_true", help="输出 JSON 快照而不是 SwiftBar 菜单")
    p.add_argument("--force", action="store_true", help="忽略 60 秒缓存，立即重新查询出口 IP")
    p.add_argument("--no-notify", action="store_true", help="不弹系统通知")
    p.add_argument("--strict", action="store_true", help="所有 IP 接口都失败时以退出码 2 结束（CI 用）")
    p.add_argument("--config", help="配置文件路径（默认 ~/.config/netwidget/exits.json）")
    p.add_argument("--action", choices=ACTIONS, help="下拉菜单里的动作")
    return p


def ensure_config(path: str) -> None:
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    example = example_config_path()
    if os.path.exists(example):
        shutil.copyfile(example, path)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"exits": [], "real_ip_hints": []}, f, ensure_ascii=False, indent=2)


def run_action(action: str, paths: app.Paths) -> int:
    if action == "refresh":
        app.force_next_refresh(paths)
    elif action == "copy-ip":
        ip = (app.load_json(paths.state, {}) or {}).get("ip")
        if ip:
            sysmod.copy_to_clipboard(ip)
    elif action == "open-config":
        ensure_config(paths.config)
        sysmod.open_text(paths.config)
    elif action == "open-history":
        if not os.path.exists(paths.history):
            app.append_history(paths.history, "# netwidget 出口变化记录（最近 200 条）")
        sysmod.open_text(paths.history)
    return 0


def main(argv: Optional[List[str]] = None, plugin_path: Optional[str] = None) -> int:
    args = build_parser().parse_args(argv)
    paths = app.Paths(config=os.path.expanduser(args.config) if args.config else None)
    if args.action:
        return run_action(args.action, paths)

    snapshot = app.collect(paths, force=args.force, notify=not args.no_notify)

    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        actions = {}
        if plugin_path:
            base = [sys.executable, plugin_path, "--action"]
            if args.config:
                base = [sys.executable, plugin_path, "--config", args.config, "--action"]
            actions = {a: base + [a] for a in ACTIONS}
        sys.stdout.write(core.render_swiftbar(snapshot, actions))
    if args.strict and not snapshot["ip"]:
        print("所有出口 IP 接口都失败了：%s" % json.dumps(snapshot["source"]["attempts"], ensure_ascii=False),
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
