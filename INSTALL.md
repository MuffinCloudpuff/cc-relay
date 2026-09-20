# cc-relay 部署与实战安装手册

本手册提供 `cc-relay` 从零开始的完整安装、跨平台配置、三大上游接入、Claude Code 注入以及常见故障排查指引。

---

## 📋 目录

1. [前置环境要求](#1-前置环境要求)
2. [获取源码与初始配置](#2-获取源码与初始配置)
3. [三大上游对接实操](#3-三大上游对接实操)
   - [3.1 DeepSeek 上游直连](#31-deepseek-上游直连)
   - [3.2 Codex (CLIProxyAPI) 桥接](#32-codex-cliproxyapi-桥接)
   - [3.3 Gemini (Antigravity Tools) 桥接](#33-gemini-antigravity-tools-桥接)
4. [Claude Code 配置注入](#4-claude-code-配置注入)
5. [Windows 无感自动化体验 (Wrapper & Lifecycle)](#5-windows-无感自动化体验-wrapper--lifecycle)
6. [macOS / Linux 守护与服务化](#6-macos--linux-守护与服务化)
7. [日常运维与诊断命令](#7-日常运维与诊断命令)
8. [常见故障排查 FAQ](#8-常见故障排查-faq)

---

## 1. 前置环境要求

- **Python 3.8 或更高版本**（必须已加入系统 PATH）
  - 验证命令：`python --version` 或 `python3 --version`
  - *注：cc-relay 采用纯标准库构建，无需安装任何 pip 扩展包。*
- **Claude Code CLI**（官方已正常安装可用）
  - 验证命令：`claude --version`
- **Git**
  - 验证命令：`git --version`

---

## 2. 获取源码与初始配置

### 2.1 克隆仓库

```bash
# 克隆仓库至本地
git clone https://github.com/xsneser/cc-relay.git
cd cc-relay
```

### 2.2 生成本地配置文件

```bash
# Windows (CMD / PowerShell)
copy config.example.json config.json

# macOS / Linux
cp config.example.json config.json
```

> ⚠️ **安全警告**：`config.json` 包含你的真实上游 API Key，严禁上传或提交至公开代码仓库！项目自带的 `.gitignore` 已默认忽略该文件。

---

## 3. 三大上游对接实操

### 3.1 DeepSeek 上游直连

1. 前往 [DeepSeek 开放平台](https://platform.deepseek.com/) 获取 API Key。
2. 打开 `config.json`，填写 `real_deepseek_key`：
   ```json
   {
     "real_deepseek_key": "sk-your-actual-deepseek-api-key"
   }
   ```
3. 默认配置下，DeepSeek 上游端点为 `https://api.deepseek.com/anthropic`，网络直连即可使用。

---

### 3.2 Codex (CLIProxyAPI) 桥接

[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) 可以将 OpenAI Codex / GPT 系列模型转换为 Anthropic Messages 兼容格式。

1. **下载与放置**：
   - 从 CLIProxyAPI 官方 Release 页面下载对应系统的二进制文件。
   - Windows 用户将 `cli-proxy-api.exe` 放入本项目的 `codex-proxy/` 目录下。
2. **生成与配置 `config.yaml`**：
   - 复制模板：
     ```bash
     cd codex-proxy
     copy config.example.yaml config.yaml
     ```
   - 编辑 `codex-proxy/config.yaml`，按需配置监听端口（默认 `8317`）与代理。
3. **完成 OAuth / Token 登录**：
   - 运行 `start_proxy.bat` 或手动执行 `cli-proxy-api.exe` 完成账号授权。
   - 验证服务正常响应：访问 `http://127.0.0.1:8317/v1/models`。
4. **绑定至 `config.json`**：
   ```json
   {
     "codex_proxy_key": "your-proxy-key",
     "codex_exe": "C:\\path\\to\\cc-relay\\codex-proxy\\cli-proxy-api.exe",
     "codex_config": "C:\\path\\to\\cc-relay\\codex-proxy\\config.yaml"
   }
   ```
   *配置 `codex_exe` 路径后，当路由命中 Codex 且代理未启动时，cc-relay 会自动在后台静默拉起该进程。*

---

### 3.3 Gemini (Antigravity Tools) 桥接

1. 确保本地 Antigravity Tools 已启动并监听在 `8045` 端口（提供 Anthropic 兼容端点）。
2. 在 `config.json` 中配置：
   ```json
   {
     "antigravity_key": "",
     "antigravity_exe": "C:\\path\\to\\antigravity-tools.exe",
     "upstreams": {
       "antigravity": {
         "base": "http://127.0.0.1:8045",
         "key_env": "antigravity_key",
         "proxy_url": "direct"
       }
     }
   }
   ```
3. 验证端点连通性：在 Web 仪表盘点击 **Gemini 探针** 或调用 `GET /api/probe?name=antigravity`。

---

## 4. Claude Code 配置注入

`cc-relay` 支持一键将中转路由所需的环境变量增量合并至用户全局 Claude 配置文件 `~/.claude/settings.json`。

### 方式 A：自动化注入脚本（推荐）

```bash
# 预览即将写入的配置（安全只读）
python apply_settings.py --dry-run

# 正式执行安全增量合并
python apply_settings.py
```

`apply_settings.py` 会动态读取当前 `config.json` 的监听端口与 `fake_api_key`，并将以下变量合并至 `settings.json` 的 `env` 节点中，**绝不丢失或覆盖已有的其他自定义环境变量**：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8400",
    "ANTHROPIC_AUTH_TOKEN": "sk-relay-local-0000",
    "ANTHROPIC_MODEL": "relay-main[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "OPUS_MODEL[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "SONNET_MODEL[1m]",
    "ANTHROPIC_SMALL_FAST_MODEL": "FAST_MODEL[1m]"
  }
}
```

### 方式 B：终端临时环境变量

如果不希望更改全局配置文件，可在各终端中单独注入：

**Windows PowerShell:**
```powershell
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:8400"
$env:ANTHROPIC_AUTH_TOKEN="sk-relay-local-0000"
$env:ANTHROPIC_MODEL="relay-main[1m]"
$env:ANTHROPIC_DEFAULT_OPUS_MODEL="OPUS_MODEL[1m]"
$env:ANTHROPIC_DEFAULT_SONNET_MODEL="SONNET_MODEL[1m]"
$env:ANTHROPIC_SMALL_FAST_MODEL="FAST_MODEL[1m]"
claude
```

**macOS / Linux Bash / Zsh:**
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8400"
export ANTHROPIC_AUTH_TOKEN="sk-relay-local-0000"
export ANTHROPIC_MODEL="relay-main[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="OPUS_MODEL[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="SONNET_MODEL[1m]"
export ANTHROPIC_SMALL_FAST_MODEL="FAST_MODEL[1m]"
claude
```

---

## 5. Windows 无感自动化体验 (Wrapper & Lifecycle)

为了让使用体验如丝般顺滑，项目内置了 Windows 环境下的包装器（Wrapper）与后台自动化机制：

### 5.1 设置 Wrapper 优先执行

1. 将 `C:\path\to\cc-relay\wrapper` 目录添加至系统环境变量 `PATH`，且**排在 npm 全局目录之前**。
2. 当你在任何控制台输入 `claude` 时：
   - `wrapper/claude.cmd` 会被优先调用；
   - 自动执行 `python lifecycle.py autostart` 秒级唤醒中转服务；
   - 自动拉起后台 `lifecycle.py watch` 守护进程；
   - 无缝转接执行原生的 Claude Code。
3. 当所有 `claude.exe` 进程退出后，看门狗在检测到空闲超时后会自动停止中转与 Codex 上游，释放系统资源。

### 5.2 桌面快捷方式

双击 `start_desktop.bat` 可快速启动服务并自动在默认浏览器中打开 Web 监控仪表盘（`http://127.0.0.1:8610`）。

---

## 6. macOS / Linux 守护与服务化

在 POSIX 环境下，你可以使用 `systemd`（Linux）或 `launchd`（macOS）将 `cc-relay` 常驻为后台用户服务。

### 6.1 Linux Systemd 用户服务配置

创建文件 `~/.config/systemd/user/cc-relay.service`：

```ini
[Unit]
Description=Claude Code Multi-Upstream Relay
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/your-user/cc-relay
ExecStart=/usr/bin/python3 cc_relay.py serve
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
```

启用与启动服务：
```bash
systemctl --user daemon-reload
systemctl --user enable --now cc-relay.service
systemctl --user status cc-relay.service
```

---

## 7. 日常运维与诊断命令

| 需求场景 | 执行命令 |
| :--- | :--- |
| **前台调试启动** | `python cc_relay.py serve` |
| **前台无 UI 启动** | `python cc_relay.py serve --no-ui` |
| **查看运行时服务与进程状态** | `python lifecycle.py status` |
| **一键停止中转与所有 Sidecar**| `python lifecycle.py stopall` |
| **查看最近 10 条调用抓包摘要**| `python cc_relay.py last 10` |
| **查看流量统计分布** | `python cc_relay.py stats` |
| **测试配置注入 Dry-Run** | `python apply_settings.py --dry-run` |

---

## 8. 常见故障排查 FAQ

### Q1: 启动时报错 `[Errno 10048]` / `Address already in use` (端口被占用)
- **原因**：8400 (Relay) 或 8610 (UI) 端口被残留进程或其它应用占用。
- **解决**：
  - Windows 执行：`python lifecycle.py stopall` 强制清理残留。
  - 或在 `config.json` 中修改 `"listen_port"` 与 `"ui_port"` 为未占用端口，并重新运行 `python apply_settings.py`。

### Q2: 提示 `401 Unauthorized` 或 `invalid api key`
- **原因**：Claude Code 传入的 Token 与 `config.json` 中的 `fake_api_key` 不匹配。
- **解决**：
  - 检查 `config.json` 中的 `"fake_api_key"`（默认 `sk-relay-local-0000`）。
  - 重新运行 `python apply_settings.py` 刷新 `~/.claude/settings.json`。

### Q3: 切换到 Codex 上游后报错 `502 Bad Gateway`
- **原因**：`codex-proxy` (CLIProxyAPI) 尚未启动，或 OAuth 登录已过期。
- **解决**：
  - 检查 `http://127.0.0.1:8317/v1/models` 是否能正常访问。
  - 手动运行 `codex-proxy/start_proxy.bat` 检查是否有登录或代理报错。

### Q4: 切换到 Gemini 上游提示 `503 No available accounts`
- **原因**：该错误直接源自 Antigravity Tools 本地端点（8045），说明当前绑定的 Google 账号配额耗尽或失效。
- **解决**：打开 Antigravity Tools 客户端刷新/重新登录 Google 账号。

### Q5: 为什么在 Web UI 中看到的 Prompt 缓存命中率较低？
- **原因**：
  1. 上游模型本身未开启或不支持 Prompt Caching；
  2. 连续两次请求的 System Prompt 或历史上下文发生了剧烈变动；
  3. 注意：`cc-relay` 的 `builtin` 修剪模式已具备断点顺延算法，不会破坏缓存连续性。
