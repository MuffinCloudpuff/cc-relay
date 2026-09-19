# cc-relay

**Claude Code 双上游统一中转** —— 让 Claude Code 同时接入 DeepSeek 与 Codex，按档位/模型名智能路由，带档位级推理强度与实时流量统计 + 抓包查看器。

```
Claude Code ──(假 sk)──▶ cc-relay :8400 ──┬──▶ DeepSeek API（直连）
                        （持真实 key）      └──▶ CLIProxyAPI :8317 ──▶ Codex（GPT）
```

---

## 特性

- **双上游统一入口**：Claude Code 只连本地中转（用假 key），凭据集中在中转，不落 CC 配置
- **五档位路由**：按 CC 的身份档位（主模型 / OPUS / SONNET / FAST / 子代理）分别路由到不同模型
- **运行时热切换**：UI 上改路由，**运行中的 CC 下一个请求即生效**，无需重启
- **档位级推理强度**：全局或每档单独设 effort，注入到请求体的 `thinking` 参数
- **实时流量统计**：按「上游 + 实际模型」聚合请求数 / 成功 / 失败
- **抓包查看器**：逐条查看「CC 发了什么 → 我们选了谁转发 → 走了哪个上游 → 命中什么规则」
- **生命周期托管**：敲 `claude` 自动拉起中转，最后一个 CC 退出后自动关闭
- **日志自动轮转**：记录文件超过 1GB 自动裁剪，防止撑爆磁盘
- **纯标准库**：中转本体零第三方依赖（仅需 Python 3）

---

## 架构

```
┌──────────────┐   假 key: sk-relay-local-0000
│ Claude Code  │──────────────┐
└──────────────┘              │  ANTHROPIC_BASE_URL=http://127.0.0.1:8400
                              ▼
                    ┌───────────────────┐
                    │  cc-relay  :8400  │  按 router.route 路由
                    │  （持真实 key）    │  · hybrid   按档位分发（5 档）
                    └─────────┬─────────┘  · deepseek 全部走 DS
                              │            · codex    全部走 Codex
              ┌───────────────┴───────────────┐
              ▼                               ▼
   ┌────────────────────┐        ┌──────────────────────┐
   │ DeepSeek API       │        │ CLIProxyAPI  :8317   │
   │ api.deepseek.com   │        │ （Codex 额度反代）     │
   └────────────────────┘        └──────────────────────┘
```

**UI**（`:8610`）与中转同进程，提供路由切换、流量统计、抓包查看。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `cc_relay.py` | **中转主程序**（转发 + 路由 + 记录 + UI 后端），一个进程同时服务 8400 和 8610 |
| `ui.html` | 路由切换 / 流量统计 / 抓包查看器页面 |
| `lifecycle.py` | 生命周期托管：`autostart` / `watch`（看门狗）/ `stopall` / `status` |
| `guardian.py` | 中转守护进程（保活） |
| `sentinel.py` | 哨兵：监控状态并记录到 `sentinel.log` |
| `config.example.json` | 配置模板（复制为 `config.json` 后填真实值） |
| `codex-proxy/` | CLIProxyAPI 反代（官方程序需自行下载，见下） |
| `INSTALL.md` | **面向新用户的 Windows 安装说明**（含 `claude` 自启动、混合模式配置、API 配置、排错） |

---

## 快速开始

> 第一次部署请看 **[INSTALL.md](INSTALL.md)** —— 逐步装完「敲 `claude` 自动拉起中转」的完整流程。下面是精简版。

### 1. 环境要求

- Windows（路径/隐藏窗口相关逻辑为 Windows 设计；中转本体逻辑跨平台）
- Python 3.8+（无需第三方包）
- [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)（`cli-proxy-api.exe`）用于 Codex 上游 —— 因体积过大未随仓库提供，请自行下载放入 `codex-proxy/`

### 2. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json` 填入：

```jsonc
{
  "real_deepseek_key": "sk-...",        // 你的 DeepSeek API Key
  "codex_proxy_key":   "your-key",      // 与 codex-proxy/config.yaml 的 api-keys 一致
  "codex_exe":  "C:\\path\\to\\codex-proxy\\cli-proxy-api.exe",
  "codex_config":"C:\\path\\to\\codex-proxy\\config.yaml"
}
```

同时复制 `codex-proxy/config.example.yaml` 为 `codex-proxy/config.yaml`，填入 CLIProxyAPI 的 `api-keys` 与上游凭据。

> ⚠️ `config.json` / `codex-proxy/config.yaml` **已在 `.gitignore` 中**，不会被提交。

### 3. 让 Claude Code 走中转

设置环境变量（用户级）：

```
ANTHROPIC_BASE_URL              = http://127.0.0.1:8400
ANTHROPIC_AUTH_TOKEN            = sk-relay-local-0000      （假 key，与 config.fake_api_key 一致）
ANTHROPIC_MODEL                 = 本地中转[1m]
ANTHROPIC_DEFAULT_OPUS_MODEL    = OPUS_MODEL[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL  = SONNET_MODEL[1m]
ANTHROPIC_SMALL_FAST_MODEL      = FAST_MODEL[1m]
```

> 这些是**占位名**，中转会剥掉 `[1m]` 后缀并按名字识别档位，再决定实际转发给谁。
> `[1m]` 同时用于向 CC 声明上下文窗口，避免「未知模型」告警。
> 基名为 `本地中转` / `relay` / `local` 时视为「无信息占位名」，归入主模型档（若判定为子代理则归子代理档）。

### 4. 启动

```bash
python cc_relay.py serve          # 启动中转 + UI（默认 8400 / 8610）
python cc_relay.py serve --no-ui  # 只启动中转
python cc_relay.py ui             # 只起 UI
```

然后打开 **http://127.0.0.1:8610**。

可选：用 `lifecycle.py` 做自动化生命周期：

```bash
python lifecycle.py autostart   # 确保中转运行（未跑则拉起）
python lifecycle.py watch       # 看门狗：最后一个 claude 退出后自动关闭中转
python lifecycle.py status      # 查看状态
python lifecycle.py stopall     # 全部停止
```

也可通过环境变量覆盖路径：`CC_RELAY_DIR`（项目根，默认脚本所在目录）、`CC_RELAY_PYTHONW`（隐藏窗口用的 pythonw.exe，默认取当前解释器同目录的 `pythonw.exe`）。

---

## 路由机制

### 三种路由策略（`router.route`）

| route | 含义 |
|---|---|
| `hybrid` | **默认**。按 CC 档位分发：OPUS 档 → 高价值模型，其余 → 各档配置的模型 |
| `deepseek` | 全部请求 → DeepSeek（可指定具体模型） |
| `codex` | 全部请求 → Codex（可指定具体模型） |

### 档位映射（hybrid）

| 档位 | CC 发出的名字 | 用途 | 默认目标 | 命中规则标签 |
|---|---|---|---|---|
| 主模型 | `本地中转[1m]` | 主对话循环 | `deepseek-flash` | `hybrid:main` |
| OPUS | `OPUS_MODEL[1m]` / `claude-opus-5` / `claude-opus-4-6` | plan / 复杂推理 | `gpt-5.6-sol` | `hybrid:opus` |
| SONNET | `SONNET_MODEL[1m]` / `claude-sonnet-5` | 子代理档 | `deepseek-flash` | `hybrid:sonnet` |
| FAST | `FAST_MODEL[1m]` / `claude-haiku` | 后台小调用 | `deepseek-flash` | `hybrid:fast` |
| 子代理 | 任意占位名 + 子代理特征 | Claude Agent SDK / 子代理 | `deepseek-flash` | `hybrid:agent` |

- 上游由**所选模型名**自动判定：`gpt-*` → Codex，其余 → DeepSeek。
- CC 直接发真实 Codex 模型名（如 `gpt-5.6-sol`）时原样透传（`hybrid:gpt-direct`）。
- **子代理识别**（`hybrid:agent`）：请求头带 `x-claude-code-agent-id`，或 system 里含 `cc_is_subagent=true` / `Claude Agent SDK` / `You are a Claude agent`（主循环的 system 是 `You are Claude Code`，不冲突）。子代理档独立于主模型档，可单独省钱或单独升级。

### 内置模型表

| 上游 | 模型 |
|---|---|
| DeepSeek | `deepseek-flash`、`deepseek-v4-pro` |
| Codex | `gpt-5.6-sol`、`gpt-5.6-luna`、`gpt-5.6-terra`、`gpt-6-astra`、`gpt-5.5`、`gpt-5.3-codex-spark` |

除内置表外，`/api/status` 会**运行时**从两个上游的 `/v1/models` 拉取真实可用模型（过滤图像类，120 秒缓存），UI 下拉即以实拉结果为准。

### 推理强度（Effort）

全局 `router.effort`，或用 `router.tier_efforts` 按档位覆盖（`main` / `opus` / `sonnet` / `fast` / `agent`）。
生效时注入到转发请求体的 `thinking` 字段：

| effort | 注入值 |
|---|---|
| `off` | `{"type":"disabled"}` |
| `on` | `{"type":"enabled"}` |
| `instant` | `enabled, budget_tokens=512` |
| `low` | `enabled, budget_tokens=2048` |
| `medium` | `enabled, budget_tokens=8192` |
| `high` | `enabled, budget_tokens=16384` |
| `xhigh` | `enabled, budget_tokens=32768` |
| `max` | `enabled, budget_tokens=65536` |

> GPT-5.6 侧经 CLIProxyAPI 转换后，budget 阈值会映射到 Codex 的 reasoning effort（实测 `xhigh`/32768 会触发思考 token）。

---

## UI 说明

打开 **http://127.0.0.1:8610**：

- **路由卡片**：DeepSeek 直连 / Codex 全量 / 混合（含 5 档下拉）
- **模型推理强度**：全局 + 档位级 effort 选择
- **代理状态条**：Codex 上游运行状态与启停
- **📊 实时模型流量**：按上游 + 实际模型聚合
- **🔍 抓包查看器**：逐条查看 CC 请求与路由决策，点行展开完整 headers/body/响应

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 全部状态（路由、档位、模型列表、流量聚合、代理状态） |
| POST | `/api/route` | 切换路由 / 档位模型 / 推理强度 |
| POST | `/api/proxy` | 启动 / 停止 Codex 上游 |
| POST | `/api/reset` | 流量清零 |
| GET | `/api/calls?n=60` | 抓包摘要列表 |
| GET | `/api/call?idx=N` | 单条调用完整内容 |

`GET /api/status` 关键字段：

```jsonc
{
  "route": "hybrid",
  "models_ds": ["deepseek-flash", "deepseek-v4-pro"],        // 运行时实拉，120s 缓存
  "models_codex": ["gpt-5.6-sol", "..."],
  "tiers":       { "main": "...", "opus": "...", "sonnet": "...", "fast": "...", "agent": "..." },
  "tier_env":    { "main": "ANTHROPIC_MODEL", "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
                   "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL", "fast": "ANTHROPIC_SMALL_FAST_MODEL",
                   "agent": "ANTHROPIC_MODEL·plan代理" },
  "efforts": ["off","on","instant","low","medium","high","xhigh","max"],
  "effort": "medium",
  "tier_efforts": { "main": "...", "opus": "...", "sonnet": "...", "fast": "...", "agent": "..." },
  "proxy_running": true, "codex_up": true,
  "rows": [{ "route": "deepseek", "model": "deepseek-flash", "sent": "...", "req": 5, "ok": 5, "err": 0, "last": 12 }],
  "total": 8
}
```

示例：

```bash
# 切换为混合模式并配置五档 + 档位级推理强度
curl -X POST http://127.0.0.1:8610/api/route -H "Content-Type: application/json" -d '{
  "route": "hybrid",
  "tiers": {"main":"deepseek-flash","opus":"gpt-5.6-sol","sonnet":"deepseek-flash",
            "fast":"deepseek-flash","agent":"gpt-5.6-sol"},
  "tier_efforts": {"main":"medium","opus":"medium","sonnet":"instant","fast":"instant","agent":"medium"}
}'

# 全部走 DeepSeek
curl -X POST http://127.0.0.1:8610/api/route -H "Content-Type: application/json" \
  -d '{"route":"deepseek","model":"deepseek-flash"}'
```

`tiers` / `tier_efforts` 只需传想改的档位，未传的保持不变（合并更新）。

---

## 命令行工具

```bash
python cc_relay.py stats        # 汇总统计（路由分布、模型分布、最近记录）
python cc_relay.py last 20      # 最近 20 条详情
python cc_relay.py dump 5       # 第 5 条完整 JSON
python cc_relay.py startproxy   # 启动 Codex 上游
python cc_relay.py stopproxy    # 停止 Codex 上游
python cc_relay.py proxycheck   # 查询 Codex 上游存活
```

---

## 日志轮转

`records.jsonl` 记录全部请求（headers / body / 路由决策 / 响应）。

- **上限 1 GB**：每次写入时检查，超限自动**删除最旧的一半**，保留最近记录
- 断点续读：只从文件尾部按需读取，适配超大单条记录（单条可达数百 KB）

---

## 安全须知

- `config.json`、`codex-proxy/config.yaml`、`.proxy_key` 含真实凭据，**已在 `.gitignore` 中**
- Codex 的 OAuth 凭证存放在 `~/.cli-proxy-api/`（CLIProxyAPI 的 `auth-dir`），**不要提交**
- Claude Code 侧只接触假 key（`sk-relay-local-0000`），真实 key 仅存在于中转进程内

---

## 更新记录

- **2026-09-10** 初版：双上游中转 + 三种路由 + 生命周期托管 + 流量统计 + 抓包查看器
- **2026-09-10** 五档位（新增子代理 `agent` 档）+ 档位级 `tier_efforts` 推理强度 + `instant/xhigh/max` 档
- 运行时模型列表实拉（`/v1/models`，120s 缓存），取代手写模型表

---

## 许可

MIT（中转与 UI 代码）。`codex-proxy/` 内的 CLIProxyAPI 为其各自项目所有，遵循其原始许可。
