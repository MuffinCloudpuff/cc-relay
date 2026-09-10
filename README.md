# cc-relay

**Claude Code 双上游统一中转** —— 让 Claude Code 同时接入 DeepSeek 与 Codex，按档位/模型名智能路由，并带实时流量统计与抓包查看器。

```
Claude Code ──(假 sk)──▶ cc-relay :8400 ──┬──▶ DeepSeek API（直连）
                        （持真实 key）      └──▶ CLIProxyAPI :8317 ──▶ Codex（GPT）
```

---

## 特性

- **双上游统一入口**：Claude Code 只连本地中转（用假 key），凭据集中在中转，不落 CC 配置
- **档位级路由**：按 CC 的身份档位（主模型 / OPUS / SONNET / FAST）分别路由到不同模型
- **运行时热切换**：UI 上改路由，**运行中的 CC 下一个请求即生效**，无需重启
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
                    │  （持真实 key）    │  · hybrid   按档位分发
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

---

## 快速开始

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

> 这些是**占位名**，中转会剥掉 `[1m]` 后缀并识别档位，再决定实际转发给谁。
> `[1m]` 同时用于向 CC 声明上下文窗口，避免「未知模型」告警。

### 4. 启动

```bash
python cc_relay.py serve          # 启动中转 + UI（默认 8400 / 8610）
python cc_relay.py serve --no-ui  # 只启动中转
```

然后打开 **http://127.0.0.1:8610**。

可选：用 `lifecycle.py` 做自动化生命周期：

```bash
python lifecycle.py autostart   # 确保中转运行（未跑则拉起）
python lifecycle.py watch       # 看门狗：最后一个 claude 退出后自动关闭中转
python lifecycle.py status      # 查看状态
python lifecycle.py stopall     # 全部停止
```

---

## 路由机制

### 三种路由策略（`router.route`）

| route | 含义 |
|---|---|
| `hybrid` | **默认**。按 CC 档位分发：OPUS 档 → 高价值模型，其余 → 统一模型 |
| `deepseek` | 全部请求 → DeepSeek（可指定具体模型） |
| `codex` | 全部请求 → Codex（可指定具体模型） |

### 档位映射（hybrid）

| 档位 | CC 发出的名字 | 用途 | 默认目标 |
|---|---|---|---|
| 主模型 | `本地中转[1m]` | 主对话循环 | `deepseek-v4-flash` |
| OPUS | `OPUS_MODEL[1m]` | plan / 复杂推理 | `gpt-5.6-sol` |
| SONNET | `SONNET_MODEL[1m]` | 子代理 | `deepseek-v4-flash` |
| FAST | `FAST_MODEL[1m]` | 后台小调用 | `deepseek-v4-flash` |

上游由**所选模型名**自动判定：`gpt-*` → Codex，其余 → DeepSeek。

### 推理强度

每个档位可单独配 `effort`（`off` / `low` / `medium` / `high` / `instant`），随请求透传。

---

## UI 说明

打开 **http://127.0.0.1:8610**：

- **路由卡片**：DeepSeek 直连 / Codex 全量 / 混合（含 4 档下拉）
- **模型推理强度**：档位级 effort 选择
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

示例：

```bash
# 切换为混合模式并配置四档
curl -X POST http://127.0.0.1:8610/api/route -H "Content-Type: application/json" -d '{
  "route": "hybrid",
  "tiers": {"main":"deepseek-v4-flash","opus":"gpt-5.6-sol",
            "sonnet":"deepseek-v4-flash","fast":"deepseek-v4-flash"}
}'

# 全部走 DeepSeek
curl -X POST http://127.0.0.1:8610/api/route -H "Content-Type: application/json" \
  -d '{"route":"deepseek","model":"deepseek-v4-flash"}'
```

---

## 命令行工具

```bash
python cc_relay.py stats        # 汇总统计（路由分布、模型分布、最近记录）
python cc_relay.py last 20      # 最近 20 条详情
python cc_relay.py dump 5       # 第 5 条完整 JSON
python cc_relay.py startproxy   # 启动 Codex 上游
python cc_relay.py stopproxy    # 停止 Codex 上游
```

---

## 日志轮转

`records.jsonl` 记录全部请求（headers / body / 路由决策 / 响应）。

- **上限 1 GB**：每次写入时检查，超限自动**删除最旧的一半**，保留最近记录
- 断点续读：只从文件尾部按需读取，适配超大单条记录

---

## 安全须知

- `config.json`、`codex-proxy/config.yaml`、`.proxy_key` 含真实凭据，**已在 `.gitignore` 中**
- Codex 的 OAuth 凭证存放在 `~/.cli-proxy-api/`（CLIProxyAPI 的 `auth-dir`），**不要提交**
- Claude Code 侧只接触假 key（`sk-relay-local-0000`），真实 key 仅存在于中转进程内

---

## 许可

MIT（中转与 UI 代码）。`codex-proxy/` 内的 CLIProxyAPI 为其各自项目所有，遵循其原始许可。
