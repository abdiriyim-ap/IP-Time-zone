#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# <xbar.title>netwidget：出口 IP / 时区 / 网速</xbar.title>
# <xbar.version>v1.0.0</xbar.version>
# <xbar.author>abdiriyim-ap</xbar.author>
# <xbar.desc>看当前出口 IP 是否是你的固定 IP，出口时区 vs 本机时区，实时网速。</xbar.desc>
# <xbar.dependencies>python3 (3.9+，只用标准库)</xbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideDisablePlugin>false</swiftbar.hideDisablePlugin>
"""SwiftBar 插件入口（薄壳）。

请用软链接安装（ln -s），这样插件能找到仓库里的 netwidget/ 包：
    ln -s "$PWD/plugin/netwidget.10s.py" "<SwiftBar 插件目录>/netwidget.10s.py"
文件名里的 10s 是 SwiftBar 的刷新间隔；出口 IP 另有 60 秒缓存。
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)

try:
    from netwidget.cli import main
except Exception as exc:  # 安装不完整时在菜单栏给出提示，而不是一片空白
    print("🔴 netwidget")
    print("---")
    print("加载失败：%s | color=#c62828" % str(exc).replace("|", "/"))
    print("请用 ln -s 软链接安装插件，不要只复制单个文件")
    print("当前 Python：%s" % sys.version.split()[0])
    sys.exit(0)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], plugin_path=os.path.abspath(__file__)))
