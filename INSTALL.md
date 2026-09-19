# 安装说明（Windows）

面向第一次部署 cc-relay 的用户。装完的效果：**终端敲 `claude` → 中转和 UI 自动拉起 → CC 正常对话 → 最后一个 CC 退出后中转自动关闭**。

> 下面以项目放在 `C:\cc-relay` 为例。路径可以换，但 `config.json` 里出现的路径要跟着换。
> 找不到命令对应关系时，先看 [第 8 节 故障排查](#8-故障排查)。

---

## 1. 前置条件

| 项 | 要求 | 备注 |
|---|---|---|
| 系统 | Windows 10 / 11 | 中转本体逻辑跨平台，但隐藏窗口/进程管理是为 Windows 写的 |
| Python | 3.8+ | 安装时勾选 **Add Python to PATH**；需要 `python.exe` 和 `pythonw.exe` 都在 PATH |
| Claude Code | 已能单独运行 | `npm i -g @anthropic-ai/claude-code`，装完 `claude --version` 有输出 |
| 网络 | 能直连 `api.deepseek.com` | DeepSeek 直连，不需要代理 |
| 代理（可选） | 本机 HTTP 代理 | 只有要接 **Codex/GPT** 才需要，例如 Clash 的 `127.0.0.1:7897` |
| ChatGPT 账号（可选） | 有 Codex 额度 | 同上，用 OAuth 登录，不消耗 API key |

**只想用 DeepSeek**：跳过第 3 节，`config.json` 里把 `route` 设成 `"deepseek"` 即可，不需要 CLIProxyAPI。

---

## 2. 获取项目

```bat
git clone https://github.com/xsneser/cc-relay.git C:\cc-relay
```

或者下载 ZIP 解压到 `C:\cc-relay`。目录里应该有 `cc_relay.py`、`lifecycle.py`、`ui.html`、`config.example.json`。

---

## 3. 配置 Codex 上游（可选）

> 跳过本节 = 只能用 DeepSeek。要用 GPT 系模型必须做这一节。

### 3.1 放好 CLIProxyAPI

1. 到 [CLIProxyAPI Releases](https://github.com/router-for-me/CLIProxyAPI/releases) 下载 Windows 版（`cli-proxy-api.exe`）
2. 放到 `C:\cc-relay\codex-proxy\cli-proxy-api.exe`

### 3.2 写 codex-proxy 配置

```bat
cd /d C:\cc-relay\codex-proxy
copy config.example.yaml config.yaml
```

编辑 `config.yaml`，至少改这几处：

```yaml
port: 8317

auth-dir: "~/.cli-proxy-api"        # OAuth 凭证落盘位置，保持默认即可

api-keys:
  - "my-local-key-0001"             # 本地调用 key，随便设；要和 config.json 的 codex_proxy_key 一致

proxy-url: "http://127.0.0.1:7897"  # 你的代理端口；没有代理就留空 ""
```

> 端口 8317 是 cc-relay 写死的 Codex 上游端口，别改（要改得同时改 `cc_relay.py`）。

### 3.3 登录 Codex 账号

```bat
cd /d C:\cc-relay\codex-proxy
cli-proxy-api.exe -codex-login
```

会弹浏览器走 OAuth 授权；成功后凭证写入 `C:\Users\<你>\.cli-proxy-api\`（**别提交、别外传**）。
无图形环境改用设备码流程：`cli-proxy-api.exe -codex-device-login`。

### 3.4 启动并自检

```bat
start_proxy.bat                     :: 隐藏窗口启动
curl http://127.0.0.1:8317/v1/models -H "Authorization: Bearer my-local-key-0001"
```

返回模型列表就说明通了。停止用 `stop_proxy.bat`。
（后面 cc-relay 也能帮你起停这个上游：UI 上的「启动代理/停止代理」，或 `python cc_relay.py startproxy`。懒加载：hybrid 下第一个走 Codex 的请求会把它拉起来。）

---

## 4. 写中转配置 config.json

```bat
cd /d C:\cc-relay
copy config.example.json config.json
```

字段含义：

| 字段 | 说明 |
|---|---|
| `listen_host` / `listen_port` | 中转监听地址，默认 `127.0.0.1:8400`。给 CC 用，别对外暴露 |
| `ui_port` | UI 端口，默认 `8610` |
| `fake_api_key` | **发给 Claude Code 的假 key**，自己随便定（如 `sk-relay-local-0000`），要和第 6 节的环境变量一致 |
| `real_deepseek_key` | 你的 DeepSeek 官方 API Key（`sk-` 开头） |
| `codex_proxy_key` | 与 `codex-proxy/config.yaml` 的 `api-keys` **完全一致** |
| `codex_exe` | `cli-proxy-api.exe` 的**绝对路径**，如 `C:\\cc-relay\\codex-proxy\\cli-proxy-api.exe` |
| `codex_config` | `config.yaml` 的**绝对路径**，如 `C:\\cc-relay\\codex-proxy\\config.yaml` |
| `record_dir` | 预留字段，当前版本未使用（记录固定写到项目目录的 `records.jsonl`） |
| `max_body_capture` | 单条记录最多抓多少字节请求体，默认 2000000 |
| `upstreams.*` | 上游地址。DeepSeek 用 `/anthropic` 结尾的兼容端点；Codex 指向 `http://127.0.0.1:8317` |
| `router` | 路由与档位，见 [第 7 节](#7-混合模式怎么配) |

> 只想用 DeepSeek：把 `router.route` 改成 `"deepseek"`，`codex_exe` / `codex_config` / `codex_proxy_key` 可以留空。
> ⚠️ JSON 里 Windows 路径要写成双反斜杠 `C:\\cc-relay\\...`，或改成正斜杠 `C:/cc-relay/...`。

---

## 5. 让 `claude` 自启动（wrapper）

原理：在 PATH 里放一个**同名 `claude.cmd`**，排在真正的 CLI（`%APPDATA%\npm\claude.cmd`）**前面**。敲 `claude` 时先命中 wrapper，它负责拉起中转、开 UI、起看门狗，最后再调用真正的 CLI。

### 5.1 放 wrapper

仓库里自带模板：**`wrapper\claude.cmd`**（ASCII + CRLF，已写好）。

**最省事的做法（零改动）**：不复制、不改文件，直接把项目里的 `wrapper` 目录加到 PATH 最前面 —— wrapper 用 `%~dp0..` 自动定位到项目根目录。

```bat
:: 例：项目在 C:\cc-relay，就把 C:\cc-relay\wrapper 排到 PATH 最前
```

**或者**把 `wrapper\claude.cmd` 复制到你自己的 bin 目录（如 `C:\Users\<你>\bin\`），并把文件里的这一行改成实际项目路径：

```bat
if "%CC_RELAY_DIR%"=="" set "CC_RELAY_DIR=C:\cc-relay"
```

模板内容（照抄也行，**必须 ASCII 内容 + CRLF 换行**）：

```bat
@echo off
rem ============================================================
rem  claude lifecycle wrapper (cc-relay)
rem  enter : ensure cc-relay(+UI) running; open UI page
rem  watch : stop relay + codex when LAST claude exits
rem  bypass: set CLAUDE_SKIP_AUTO=1
rem ============================================================
setlocal
if "%CLAUDE_SKIP_AUTO%"=="1" goto run
if "%CC_RELAY_DIR%"=="" set "CC_RELAY_DIR=%~dp0.."
python "%CC_RELAY_DIR%\lifecycle.py" autostart
set RC=%errorlevel%
if %RC%==0 start "" "http://127.0.0.1:8610"
start "" pythonw "%CC_RELAY_DIR%\lifecycle.py" watch
:run
call "%APPDATA%\npm\claude.cmd" %*
```

- `%~dp0` 是 wrapper 自己所在目录；`%~dp0..` = 它的上一级（模板放在项目的 `wrapper\` 里，正好是项目根）
- `python` / `pythonw` 需要在 PATH 里；极端情况下可换成绝对路径
- 想临时绕过整套自动流程：`set CLAUDE_SKIP_AUTO=1` 后再敲 `claude`

### 5.2 让这个目录排在 PATH 前面

图形界面：`Win+R` → `sysdm.cpl` → 高级 → 环境变量 → 用户变量 `Path` → 编辑 → 新建 `C:\Users\<你>\bin` → **上移到最顶** → 确定。

命令行（**直接前置、保留原有 PATH**，别用 `setx`，它超过 1024 字符会截断）：

```powershell
$d = "$env:USERPROFILE\bin"
$p = [Environment]::GetEnvironmentVariable('Path','User')
if ($p -notlike "*$d*") { [Environment]::SetEnvironmentVariable('Path', "$d;$p", 'User') }
```

验证（**新开一个终端**）：

```bat
where claude
```

第一条应该指向 `C:\Users\<你>\bin\claude.cmd`，第二条才是 `%APPDATA%\npm\claude.cmd`。

---

## 6. 给 Claude Code 设环境变量

用户级设置（`cmd` 里逐条执行）：

```bat
setx ANTHROPIC_BASE_URL "http://127.0.0.1:8400"
setx ANTHROPIC_AUTH_TOKEN "sk-relay-local-0000"
setx ANTHROPIC_MODEL "relay-main[1m]"
setx ANTHROPIC_DEFAULT_OPUS_MODEL "OPUS_MODEL[1m]"
setx ANTHROPIC_DEFAULT_SONNET_MODEL "SONNET_MODEL[1m]"
setx ANTHROPIC_SMALL_FAST_MODEL "FAST_MODEL[1m]"
```

要点：

- `ANTHROPIC_AUTH_TOKEN` 必须等于 `config.json` 的 `fake_api_key`（假的，只为过 CC 的校验）。
- **`OPUS_MODEL` / `SONNET_MODEL` / `FAST_MODEL` 这三个名字必须原样保留**——中转靠名字识别档位。
- `ANTHROPIC_MODEL`（主模型档）名字随意，认不出来就归主模型档。作者本机用的是中文 `本地中转[1m]`；若遇到中文环境变量编码问题，用 `relay-main[1m]` 这类 ASCII 名即可。
- `[1m]` 后缀只做两件事：告诉 CC 上下文窗口是 1M（避免「未知模型」告警）+ 让中转知道这是占位名。中转会剥掉 `[...]` 再路由，不是必须，但建议留着。
- **`setx` 只对新开的终端生效**，设完关掉旧窗口。想恢复默认模型，删掉这几个变量即可。

---

## 7. 混合模式怎么配

`config.json` 的 `router` 段：

```jsonc
"router": {
  "route": "hybrid",
  "hybrid_codex_model": "gpt-5.6-sol",     // 高价值兜底模型
  "hybrid_deepseek_model": "deepseek-flash", // 普通兜底模型
  "tiers": {
    "main":   "deepseek-flash",   // 主对话循环
    "opus":   "gpt-5.6-sol",      // plan / 复杂推理（贵，值得）
    "sonnet": "deepseek-flash",   // 子代理档
    "fast":   "deepseek-flash",   // 后台小调用
    "agent":  "deepseek-flash"    // 子代理（Claude Agent SDK / plan 代理）
  },
  "tier_efforts": {               // 可选：每档单独设推理强度
    "main": "medium", "opus": "medium",
    "sonnet": "instant", "fast": "instant", "agent": "medium"
  },
  "effort": "medium",             // 全局兜底强度（档位没配时用这个）
  "strip_cc_banner": {            // 各档指纹清理（见下），与路由档位无关
    "main": true, "opus": false, "sonnet": false, "fast": true, "agent": false
  }
}
```

**指纹清理（`strip_cc_banner`）**：五档各自一个开关，某档开启后只对命中该档的请求做一次 body 改写——删掉 system 里整块的 CC / Agent SDK 身份句（`You are Claude Code, Anthropic's official CLI for Claude.` 或 `You are a Claude agent, built on Anthropic's Claude Agent SDK.`）和 `x-anthropic-billing-header:` 开头的 billing 指纹块，身份句混在别的文本里则只摘句子；被删块的 `cache_control` 会顺延给其后最近的幸存块（该处已有断点就不动，被删的是尾块则向前落），不会白白丢 prompt-cache 断点。UI 上就是每张档位卡右上角那个小开关，也可以 `curl -d '{"strip_cc_banner":{"main":true}}'` 按档改；传单个 `true`/`false` 等价于只改 `main`（旧的单值配置也会自动迁移为主档 + fast 档）。抓包列表里该请求会显示删了几块。

**档位怎么落到 CC 上**：

| 档位 | CC 发出的名字 | 什么时候用 | 经验配法 |
|---|---|---|---|
| `main` | `ANTHROPIC_MODEL` | 主对话循环 | 便宜快的模型 |
| `opus` | `OPUS_MODEL` | plan / 复杂推理 | 唯一值得花贵模型额度的地方 |
| `sonnet` | `SONNET_MODEL` | CC 内部中等调用 | 便宜模型 |
| `fast` | `FAST_MODEL` | 后台小调用（起标题等） | 最便宜，`instant` 强度 |
| `agent` | 任意占位名 + 子代理特征 | 子代理 / Claude Agent SDK | 按钱包决定 |

上游由**模型名前缀**决定：`gpt-*` → Codex，其余 → DeepSeek。所以任意档位都能填任一上游的模型，混搭是允许的。

**推理强度**取值 `off` / `on` / `instant` / `low` / `medium` / `high` / `xhigh` / `max`，会注入到请求体的 `thinking` 参数（`instant`=512、`low`=2048、`medium`=8192、`high`=16384、`xhigh`=32768、`max`=65536 tokens）。

**三种改法，都不需要重启 CC 或中转**（中转每个请求重读配置）：

1. **UI（推荐）**：打开 <http://127.0.0.1:8610> → 点卡片切模式 / 下拉改档位模型 → 下一个请求即生效
2. **命令行**：
   ```bat
   curl -X POST http://127.0.0.1:8610/api/route -H "Content-Type: application/json" -d "{\"route\":\"hybrid\",\"tiers\":{\"opus\":\"gpt-5.6-sol\"}}"
   ```
3. **直接编辑 `config.json`** → 保存即可

**常见配方**：

| 目标 | 怎么配 |
|---|---|
| 省钱（默认） | 只有 `opus` 用 `gpt-5.6-sol`，其余全 `deepseek-flash` |
| plan 代理也吃好模型 | `agent` 改成 `gpt-5.6-sol` |
| 全 DeepSeek | `route` 改成 `"deepseek"`（`model` 可指定具体模型） |
| 全 Codex | `route` 改成 `"codex"`，`model` 填 `gpt-5.6-sol` |
| 临时对比效果 | UI 上直接切卡片，看完再切回来，历史都在抓包查看器里 |

---

## 8. 验证与日常使用

**首次验证**：

```bat
cd /d C:\cc-relay
python lifecycle.py status
```

期望输出里 `relay_up: true`、`ui_up: true`（`codex_up` 只在要用 Codex 时关注）。

然后**新开一个终端**敲：

```bat
claude
```

应该看到：UI 浏览器页自动打开 → CC 正常进入对话。随便问一句，回到 UI 的「🔍 抓包查看器」应能看到这条请求和它命中的规则（如 `hybrid:main`）。

**日常**：

| 操作 | 命令 |
|---|---|
| 敲 `claude` | 自动拉起中转 + UI，退出后自动停 |
| 关掉浏览器页 | 不影响中转 |
| 临时不走中转 | `set CLAUDE_SKIP_AUTO=1` 后敲 `claude` |
| 手动全停 | `python lifecycle.py stopall` |
| 看状态 | `python lifecycle.py status` |
| 看抓包统计 | `python cc_relay.py stats` / `last 20` / `dump 5` |
| 只想起中转不起 UI | `python cc_relay.py serve --no-ui` |

---

## 9. 卸载 / 关掉自启动

1. 删掉 `C:\Users\<你>\bin\claude.cmd`（或把该目录从 PATH 里移除）
2. 删掉第 6 节那几个 `ANTHROPIC_*` 环境变量（`setx ANTHROPIC_MODEL ""` 之类，或图形界面删）
3. `python lifecycle.py stopall`，然后删掉整个项目目录
4. 如已登录过 Codex，按需删除 `C:\Users\<你>\.cli-proxy-api\`

---

## 10. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| 敲 `claude` 没反应 / CC 报连接失败 | 中转没起来。`python lifecycle.py status` 看 `relay_up`；直接 `python cc_relay.py serve` 看报错 |
| 「改了配置没生效」 | 旧实例还占着 8400/8610。`netstat -ano \| findstr ":8400 :8610"` 找到 PID，`taskkill /F /PID <pid>` 后再起 |
| CC 提示未知模型 / 上下文窗口不对 | 模型名带上 `[1m]` 后缀 |
| DeepSeek 报 401 | `real_deepseek_key` 不对，或 `upstreams.deepseek.base` 被改坏了 |
| Codex 报 401 / 403 | ① 没登录：`cli-proxy-api.exe -codex-login`；② `codex_proxy_key` 与 `config.yaml` 的 `api-keys` 不一致；③ 8317 没起来 |
| Codex 请求超时 / 连不上上游 | 代理没开。检查 `codex-proxy/config.yaml` 的 `proxy-url` 端口，或系统代理是否在运行 |
| UI 打不开 | 8610 被占或中转没起；`python cc_relay.py ui` 单独起 UI 试试 |
| 托盘/黑窗一闪而过 | wrapper 里的中文或 LF 换行会让 `cmd` 崩。改成纯 ASCII + CRLF |
| 想彻底重置抓包 | UI 上「清零」，或删 `records.jsonl`（会同时丢历史） |

---

## 附：最小验证清单

- [ ] `where claude` 第一条是 wrapper
- [ ] `python lifecycle.py status` → `relay_up: true`、`ui_up: true`
- [ ] 敲 `claude` → UI 自动弹出，CC 能正常回复
- [ ] UI 抓包查看器能看到刚才那条请求，规则标签符合预期（`hybrid:main` / `hybrid:opus` …）
- [ ] 退出 CC ~10 秒后中转自动关闭（`python lifecycle.py status` → false）
