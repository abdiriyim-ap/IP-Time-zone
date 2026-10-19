# IP-Time-zone · netwidget

macOS 菜单栏小组件（[SwiftBar](https://github.com/swiftbar/SwiftBar) 插件）：一眼看清**现在从哪个 IP 出去、它是不是你的固定 IP、出口时区和本机时区差多少、实时网速**。为「经常出差 + 全局代理（Quantumult X）+ 固定出口 IP」的用法设计。

```
🟢 🇯🇵 东京-固定A ↓2.3M ↑180K
```

- 只用 Python 3.9+ 标准库，不用 `pip install` 任何东西（macOS 自带的 `/usr/bin/python3` 就能跑）
- 不依赖 Quantumult X 的任何接口，换别的代理客户端也照样能用
- 设计方案见 [docs/设计方案.md](docs/设计方案.md)

## 它做什么

**菜单栏**：状态灯 + 出口国旗 + 白名单标签（没命中时显示国家代码）+ 实时 ↓/↑ 网速。

**下拉面板**：

| 区块 | 内容 |
|---|---|
| 出口 | 出口 IP、国家/地区/城市、运营商、ASN、状态说明 |
| 时区 | 出口所在时区和当地时间、本机系统时区和时间，两者不一致时标橙色提示 |
| 网速 | 物理网卡（en0 等）的实时下行/上行 |
| 辅助信号 | Quantumult X 进程是否在跑、是否有带 IPv4 的 utun（TUN）网卡，仅供参考 |
| 动作 | 立即刷新、复制出口 IP、编辑白名单、查看变化记录 |

**系统通知**（同一事件 10 分钟内只提醒一次）：

- 出口切换到另一个白名单 IP：「出口已从 东京-固定A 切换到 新加坡-固定B」
- 出口不在白名单：「当前出口 x.x.x.x（美国）不在你的固定 IP 列表」
- 出口落在你的直连网段：「代理可能已断开……」

**变化记录**：最近 200 次出口变化（含掉线/恢复），存在 `~/Library/Application Support/netwidget/history.log`。

## 状态灯怎么判断

| 灯 | 含义 | 条件 |
|---|---|---|
| 🟢 | 出口正确 | 出口 IP 命中 `exits` 白名单（支持单个 IP 或 CIDR 网段） |
| 🟡 | 未知出口 | 拿到了出口 IP，但不在白名单，也不在直连网段（比如切到了陌生节点） |
| 🔴 | 疑似直连 | 出口 IP 落在 `real_ip_hints`（你家里/公司/手机热点的网段）→ 代理大概率掉了 |
| 🔴 | 离线 | 所有 IP 查询接口都失败（断网，或代理把这些接口挡了） |

白名单命中优先于直连网段：两个都配了同一个 IP 时按 🟢 算。出口时区和本机时区不一致（出差很常见）**只在面板里标色，不弹通知**。

## 安装

1. 安装 SwiftBar：`brew install --cask swiftbar`（或去 [Releases](https://github.com/swiftbar/SwiftBar/releases) 下载），第一次打开时选一个插件目录，比如 `~/SwiftBarPlugins`。
2. 把本仓库放到固定位置并软链接插件（**要用 `ln -s`，不要只复制单个文件**，插件需要找到仓库里的 `netwidget/` 目录）：

   ```bash
   git clone https://github.com/abdiriyim-ap/IP-Time-zone.git ~/Code/IP-Time-zone
   chmod +x ~/Code/IP-Time-zone/plugin/netwidget.10s.py
   ln -s ~/Code/IP-Time-zone/plugin/netwidget.10s.py ~/SwiftBarPlugins/netwidget.10s.py
   ```

3. 写配置（见下），在 SwiftBar 菜单里点「Refresh All」。

先在终端试一下：

```bash
python3 ~/Code/IP-Time-zone/plugin/netwidget.10s.py           # SwiftBar 看到的输出
python3 ~/Code/IP-Time-zone/plugin/netwidget.10s.py --json    # 完整 JSON 快照
```

> 如果 SwiftBar 里报找不到 `python3`，把插件第一行改成 `#!/usr/bin/python3`。
> 如果用的是 python.org 安装包的 Python 且 https 报证书错误，运行一次它自带的 `Install Certificates.command`。

## 配置

配置文件：`~/.config/netwidget/exits.json`（JSON，标准库就能解析；可用环境变量 `NETWIDGET_CONFIG` 改路径）。最快的办法是点下拉菜单里的「编辑白名单…」，没有配置时会自动用 `config.example.json` 生成一份再打开。

```json
{
  "exits": [
    {"ip": "203.0.113.10", "label": "东京-固定A", "expect_tz": "Asia/Tokyo"},
    {"ip": "198.51.100.22", "label": "新加坡-固定B", "expect_tz": "Asia/Singapore"},
    {"ip": "203.0.113.64/29", "label": "洛杉矶-固定段", "expect_tz": "America/Los_Angeles"}
  ],
  "real_ip_hints": ["192.0.2.0/24"],
  "refresh_ip_seconds": 60,
  "notify": true
}
```

| 字段 | 说明 |
|---|---|
| `exits[].ip` | 你的固定出口 IP，也可以写网段（CIDR） |
| `exits[].label` | 菜单栏显示的名字 |
| `exits[].expect_tz` | 这个出口应该在的时区（IANA 名）。地理接口查不到时区时用它兜底；查到的时区和它不一致时在面板里提示 |
| `real_ip_hints` | 你的直连网络出口网段（家宽、公司、手机热点……），出口落在这里就判定为 🔴 疑似直连 |
| `refresh_ip_seconds` | 多久重新查一次出口 IP，默认 60，最小 15 |
| `notify` | 是否弹系统通知 |

`real_ip_hints` 怎么填：临时关掉代理，访问 <https://ipinfo.io> 看一下自己的 IP，填它所在的网段（比如运营商给的 IP 经常变，填 `/16` 或 `/24` 更稳）。

示例里的 IP 都是文档专用地址（RFC 5737），不是真实 IP。

## 工作方式

- 插件文件名里的 `10s` 是 SwiftBar 刷新间隔：网速每 10 秒更新（10 秒平均值）。第一次运行会现场采样 1 秒。
- 出口 IP 默认 60 秒查一次（离线时每次都重试）：先用轻量接口查 IP（ipify → checkip.amazonaws.com），再按 IP 查地理信息（ipwho.is → ip-api.com → ipinfo.io），任何一家失败/限流就换下一家。同一个 IP 的地理信息缓存 24 小时。
- 网速只统计物理网卡（en*），不算 utun，避免 TUN 和物理网卡重复计数。
- 缓存：`~/Library/Caches/netwidget/`；历史：`~/Library/Application Support/netwidget/history.log`。

## 关于 Quantumult X

- QX Mac 版走 TUN 接管全局流量，没有本地代理端口，也没有公开的「当前节点」接口，所以本组件**不读 QX 状态**，而是直接请求公网 IP 接口（流量自然经过 QX）再对白名单。对固定 IP 用法来说这反而更准：关心的是「出去的 IP 对不对」。
- 如果 QX 的分流规则把这些查询域名设成了 `DIRECT`，查到的就是直连 IP，而不是代理出口。请确保 `api.ipify.org`、`checkip.amazonaws.com`、`ipwho.is`、`ip-api.com`、`ipinfo.io` 走代理（全局模式下默认就是）。
- 「辅助信号」里的 TUN 检测只认**带 IPv4 地址**的 utun：macOS 默认就有 utun0~utun3（iCloud 等系统服务用），不代表代理开着。QX 未运行 + 没有 TUN + 出口不在白名单时会提示「代理很可能没开」，但这只是提示，不改变状态灯。

## 隐私

- 所有数据只存在本机，不上传任何地方。查询接口只会看到你的出口 IP（本来就是公开的）。
- **不要把真实 IP 提交到仓库**。`.gitignore` 已忽略 `exits.json`、`*.local.json`；配置默认放在 `~/.config/netwidget/`，本来就不在仓库里。
- 即使仓库是私有的，也建议不要把白名单放进公开同步盘。

## 开发

```bash
python3 -m unittest discover -s tests -t . -v    # 单元测试（纯标准库）
python3 plugin/netwidget.10s.py --once --json --no-notify --strict
```

- `netwidget/core.py`：纯函数（白名单 / 网段匹配、状态判断、netstat 解析、网速、时区、SwiftBar 渲染），无 I/O
- `netwidget/providers.py`：IP / 地理查询和回退
- `netwidget/app.py`：编排（缓存、通知去重、历史）
- `netwidget/system.py`：系统命令薄封装（netstat、ifconfig、pgrep、osascript、pbcopy、open）
- `--strict`：所有出口 IP 接口都失败时退出码 2，供 CI 使用

GitHub Actions（`.github/workflows/ci.yml`）在 Ubuntu（Python 3.9 / 3.13）和 macOS 上跑单元测试，并在 macOS runner 上真实联网跑一次插件，把菜单栏输出和 JSON 打到日志里。注意 runner 的出口是 GitHub 数据中心 IP，不是你的。

## 路线

- v1（当前）：出口 IP + 白名单 + 时区 + 网速 + 通知 + 历史；QX 进程 / TUN 辅助信号
- 以后可选：多接口结果交叉校验、手动带宽测速、近 5 分钟网速折线、原生 Swift 菜单栏应用
