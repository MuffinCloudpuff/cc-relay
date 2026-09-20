#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CC 统一中转 (unified relay)
- Claude Code 用假 sk 连本服务; 本服务持真实 key, 按 model 名路由到双上游:
    deepseek -> https://api.deepseek.com/anthropic (直连)
    codex    -> CLIProxyAPI 127.0.0.1:8317 (Codex 额度, 需 clash; 随需自动拉起)
- 完整 dump 每次请求 (headers/body/路由决策/响应) 供分析
- 纯 stdlib

用法:
    python cc_relay.py serve
    python cc_relay.py stats | last [n] | dump <idx>
    python cc_relay.py startproxy | stopproxy     # 手动管理 codex 上游
"""
import os, sys, json, time, threading, argparse, subprocess, socket
import urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE = os.environ.get("CC_RELAY_DIR") or os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(BASE, "config.json")
RECORDS = os.path.join(BASE, "records.jsonl")
LOCK = threading.Lock()
_IDX = [0]
_UP = {"last": "", "last_model": "", "last_ts": 0}


def load_conf():
    with open(CONF, encoding="utf-8") as f:
        return json.load(f)


CONF_LOCK = threading.Lock()


def _save_conf(conf):
    """原子写回配置: 先写 .tmp 再 replace, 免并发丢更新 / 读到半份 JSON"""
    tmp = CONF + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONF)


MAX_RECORD_BYTES = 1024 * 1024 * 1024   # 1 GB 上限
KEEP_RATIO = 0.5                        # 超限时保留最近一半
_last_check = [0.0]


def _rotate_if_needed():
    """超过 MAX_RECORD_BYTES 时删除最旧的一半(保留最近记录)"""
    try:
        size = os.path.getsize(RECORDS)
    except Exception:
        return
    if size <= MAX_RECORD_BYTES:
        return
    keep_bytes = int(size * KEEP_RATIO)
    try:
        with open(RECORDS, "rb") as f:
            f.seek(size - keep_bytes)
            rest = f.read()
        # 丢弃首个不完整行
        nl = rest.find(b"\n")
        if nl >= 0:
            rest = rest[nl + 1:]
        tmp = RECORDS + ".tmp"
        with open(tmp, "wb") as f:
            f.write(rest)
        os.replace(tmp, RECORDS)
    except Exception:
        pass


def record(entry):
    with LOCK:
        _IDX[0] += 1
        entry["idx"] = _IDX[0]
        with open(RECORDS, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # 实时轮转: 每条都检查(用缓存值快速跳过)
        if time.time() - _last_check[0] > 2.0:
            _last_check[0] = time.time()
            _rotate_if_needed()


def read_records():
    out = []
    if os.path.exists(RECORDS):
        with open(RECORDS, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    return out


# ---------- codex 上游 (CLIProxyAPI) 管理 ----------

def _tcp(port, host="127.0.0.1", t=0.6):
    try:
        s = socket.create_connection((host, port), timeout=t); s.close(); return True
    except Exception:
        return False


def codex_up():
    return _tcp(8317)


def codex_start(conf):
    if codex_up():
        return "already"
    exe = conf.get("codex_exe"); cfg = conf.get("codex_config")
    PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    cmd = (f"Start-Process -WindowStyle Hidden -FilePath '{exe}' "
           f"-ArgumentList '--config','{cfg}' -WorkingDirectory '{os.path.dirname(exe)}' "
           f"-RedirectStandardOutput '{os.path.dirname(exe)}\\proxy.out.log' "
           f"-RedirectStandardError '{os.path.dirname(exe)}\\proxy.err.log'")
    try:
        subprocess.Popen([PS, "-NoProfile", "-Command", cmd], creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return "start-failed"
    for _ in range(40):
        time.sleep(0.5)
        if codex_up():
            return "started"
    return "timeout"


def codex_stop():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "cli-proxy-api.exe"], capture_output=True,
                       text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        return "stopped"
    except Exception as e:
        return repr(e)


# ---------- 路由决策 ----------

CODEX_MODELS = ["gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-astra", "gpt-5.5",
                "gpt-5.3-codex-spark"]
DEEPSEEK_MODELS = ["deepseek-flash", "deepseek-v4-pro"]
ALL_MODELS = DEEPSEEK_MODELS + CODEX_MODELS
DEFAULT_CODEX_MAP = "gpt-5.6-sol"
DEFAULT_DS_MAP = "deepseek-flash"
# 推理强度 -> 请求 body 的 thinking 参数
# 档位依据: GPT-5.6 API 支持 none/low/medium/high/xhigh/max; 经 CLIProxyAPI 转换后
#   budget 阈值映射到 codex 的 reasoning effort(本地实测 xhigh/32768 触发思考 token)
REASONING_MAP = {
    "off":     {"type": "disabled"},
    "on":      {"type": "enabled"},
    "instant": {"type": "enabled", "budget_tokens": 512},
    "low":     {"type": "enabled", "budget_tokens": 2048},
    "medium":  {"type": "enabled", "budget_tokens": 8192},
    "high":    {"type": "enabled", "budget_tokens": 16384},
    "xhigh":   {"type": "enabled", "budget_tokens": 32768},
    "max":     {"type": "enabled", "budget_tokens": 65536},
}
EFFORT_VALUES = tuple(REASONING_MAP)
REASONING_LABELS = {"off": "关闭", "on": "开启", "instant": "即时", "low": "低", "medium": "中",
                    "high": "高", "xhigh": "极高", "max": "最大"}
_MODELS_CACHE = {"ts": 0, "ds": list(DEEPSEEK_MODELS), "cx": list(CODEX_MODELS)}


def _fetch_models(base, key, timeout=6):
    """从上游 /v1/models 拉模型名(过滤图像类)"""
    try:
        import urllib.request as _u
        req = _u.Request(base.rstrip("/") + "/v1/models")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        d = json.load(_u.urlopen(req, timeout=timeout))
        out = []
        for m in d.get("data", []):
            mid = m.get("id") or ""
            if "image" in mid or "auto-review" in mid:
                continue
            out.append(mid)
        return sorted(out)
    except Exception:
        return []


def live_models(conf, ttl=120):
    """实时模型列表(带缓存): 优先从各上游拉, 失败回退常量"""
    now = time.time()
    if now - _MODELS_CACHE["ts"] < ttl:
        return _MODELS_CACHE
    ups = conf.get("upstreams") or {}
    ds = []
    cx = []
    u = ups.get("deepseek") or {}
    ds = _fetch_models(u.get("base", ""), conf.get(u.get("key_env") or "", ""))
    u = ups.get("codex") or {}
    cx = _fetch_models(u.get("base", ""), conf.get(u.get("key_env") or "", ""))
    # codex 上游(CLIProxyAPI)同时代理了 deepseek, 剔除其模型, 只保留 codex/gpt 系
    cx = [m for m in cx if not m.startswith("deepseek-")]
    if not ds:
        ds = list(DEEPSEEK_MODELS)
    if not cx:
        cx = list(CODEX_MODELS)
    _MODELS_CACHE.update({"ts": now, "ds": ds, "cx": cx})
    return _MODELS_CACHE


def _strip_model_suffix(model):
    """剥离 CC 附加的后缀: deepseek-chat[1m] / 本地中转[1m] -> 基名"""
    if not model:
        return "", ""
    import re as _re
    m = _re.match(r'^\s*(.+?)\s*\[([^\]]+)\]\s*$', model)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return model.strip(), ""


def _is_subagent(headers, body_json):
    """识别子代理: 请求头带 x-claude-code-agent-id, 或 system 里 billing header
    含 cc_is_subagent=true, 或提示含 'Claude Agent SDK' / 'You are a Claude agent'
    (主循环的 system 是 'You are Claude Code', 不冲突)"""
    try:
        if (headers or {}).get("x-claude-code-agent-id"):
            return True
        sys = (body_json or {}).get("system")
        txt = ""
        if isinstance(sys, str):
            txt = sys
        elif isinstance(sys, list):
            txt = " ".join((c.get("text") or "") for c in sys if isinstance(c, dict))
        return ("cc_is_subagent=true" in txt) or ("Claude Agent SDK" in txt) or ("You are a Claude agent" in txt)
    except Exception:
        return False


# ---------- CC 指纹清理 (每档独立开关) ----------
CC_ID_PAT = (r"(?:You are Claude Code,?\s*Anthropic's official CLI for Claude\.?"
             r"|You are a Claude agent,\s*built on Anthropic's Claude Agent SDK\.?)")
CC_BILLING_PREFIX = "x-anthropic-billing-header:"

TIER_KEYS = ("main", "opus", "sonnet", "fast", "agent")
# 旧配置 strip_cc_banner=true 的迁移去向(主档 + fast 档)
LEGACY_STRIP_DEFAULTS = {"main": True, "opus": False, "sonnet": False, "fast": True, "agent": False}


def _strip_flags(router):
    """开关归一化 -> 五键布尔对象 (读接口 / 生效判断 / 写入合并的唯一入口)"""
    v = router.get("strip_cc_banner")
    if isinstance(v, dict):
        return {k: v.get(k) is True for k in TIER_KEYS}   # 严格布尔, 防 "false" 被当成真
    if v is True:
        return dict(LEGACY_STRIP_DEFAULTS)
    return {k: False for k in TIER_KEYS}


def _tier_from_reason(reason):
    """reason -> 档位名; 非档位(hybrid:gpt-direct / route:*)返回 None"""
    if not isinstance(reason, str) or not reason.startswith("hybrid:"):
        return None
    t = reason[len("hybrid:"):]
    return t if t in TIER_KEYS else None


def _strip_cc_fingerprint(body):
    """删除 system 里的 CC 身份句与 billing 指纹块, 返回 (新 body, 删除块数)
    - 整块命中 -> 整块丢弃; 句子混在别的文本里 -> 只摘句子
    - 被删块的 cache_control 顺延给其后第一个没有该标记的幸存块, 免得白白丢 prompt-cache 断点
    """
    import re as _re
    sysv = body.get("system")
    if sysv is None:
        return body, 0

    # 客户端直接发字符串形式
    if isinstance(sysv, str):
        ns = _re.sub(CC_ID_PAT, "", sysv)
        if ns == sysv:
            return body, 0
        nb = dict(body)
        if ns.strip():
            nb["system"] = ns
        else:
            nb.pop("system", None)
        return nb, 1

    if not isinstance(sysv, list):
        return body, 0

    kept = []
    carried = []   # (被删块本该落到的下标, cache_control)
    removed = 0
    for blk in sysv:
        txt = blk.get("text") if isinstance(blk, dict) else None
        if isinstance(txt, str):
            t = txt.strip()
            if t.startswith(CC_BILLING_PREFIX) or _re.fullmatch(CC_ID_PAT, t):
                removed += 1
                if blk.get("cache_control"):
                    carried.append((len(kept), blk["cache_control"]))
                continue
            if _re.search(CC_ID_PAT, txt):
                nb_blk = dict(blk)
                nb_blk["text"] = _re.sub(CC_ID_PAT, "", txt)
                removed += 1
                kept.append(nb_blk)
                continue
        kept.append(blk)

    if not removed:
        return body, 0

    # 缓存断点顺延: 优先落给其后最近的幸存块; 该处已有断点则不动;
    # 被删的是尾块时向前落到最近的幸存块
    for pos, cc in carried:
        placed = False
        for i in range(pos, len(kept)):
            blk = kept[i]
            if not isinstance(blk, dict):
                continue
            if blk.get("cache_control"):
                placed = True          # 已有断点覆盖该位置, 不必再往后推
                break
            nb_blk = dict(blk)
            nb_blk["cache_control"] = cc
            kept[i] = nb_blk
            placed = True
            break
        if placed:
            continue
        for i in range(min(pos, len(kept)) - 1, -1, -1):
            blk = kept[i]
            if not isinstance(blk, dict):
                continue
            if blk.get("cache_control"):
                break
            nb_blk = dict(blk)
            nb_blk["cache_control"] = cc
            kept[i] = nb_blk
            break

    nb = dict(body)
    if kept:
        nb["system"] = kept
    else:
        nb.pop("system", None)   # 别发 "system": []
    return nb, removed


# ---------- 自定义请求修改器 (热重载与安全加载) ----------
MODIFIER_FILE = os.path.join(BASE, "custom_modifier.py")


class ModifierManager:
    """管理 custom_modifier.py 的热重载与安全调用"""
    def __init__(self):
        self._mod = None
        self._mtime = 0.0
        self._err = None

    def get_modifier(self):
        """检查 custom_modifier.py 修改时间，有变更时自动热重载"""
        try:
            if not os.path.exists(MODIFIER_FILE):
                return None
            mtime = os.path.getmtime(MODIFIER_FILE)
            if mtime != self._mtime or self._mod is None:
                import importlib.util
                spec = importlib.util.spec_from_file_location("custom_modifier", MODIFIER_FILE)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    self._mod = mod
                    self._mtime = mtime
                    self._err = None
        except Exception as e:
            self._err = repr(e)
            print(f"[WARN] 加载 custom_modifier.py 失败: {e}", file=sys.stderr)
        return self._mod


_MOD_MGR = ModifierManager()


# ---------- 各档位 System 提示词管理 (复用运行时抓包 + 在线编辑) ----------
PROMPTS_FILE = os.path.join(BASE, "prompts.json")
PROMPTS_LOCK = threading.Lock()


class PromptManager:
    """管理各档位 System 提示词的抓包捕获、在线编辑与请求体替换"""
    def __init__(self):
        self._data = {
            "captured": {k: "" for k in TIER_KEYS},
            "custom": {k: "" for k in TIER_KEYS},
            "enabled": {k: False for k in TIER_KEYS},
        }
        self.load()

    def load(self):
        with PROMPTS_LOCK:
            if os.path.exists(PROMPTS_FILE):
                try:
                    with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                        d = json.load(f)
                        for section in ("captured", "custom", "enabled"):
                            if isinstance(d.get(section), dict):
                                self._data[section].update(d[section])
                except Exception:
                    pass
            else:
                self._preseed_from_records()

    def _preseed_from_records(self):
        """若无 prompts.json, 自动从历史 records.jsonl 提取各档位捕获的原始 system 提示词"""
        if not os.path.exists(RECORDS):
            return
        try:
            with open(RECORDS, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        reason = r.get("route_reason") or ""
                        tier = reason.replace("hybrid:", "") if reason.startswith("hybrid:") else None
                        if tier not in TIER_KEYS:
                            continue
                        b = r.get("body") or {}
                        sys = b.get("system")
                        if sys and not self._data["captured"][tier]:
                            if isinstance(sys, list):
                                txt = "\n\n".join(blk.get("text", "") for blk in sys if isinstance(blk, dict) and blk.get("text"))
                            else:
                                txt = str(sys)
                            self._data["captured"][tier] = txt
                    except Exception:
                        pass
            self.save()
        except Exception:
            pass

    def save(self):
        with PROMPTS_LOCK:
            tmp = PROMPTS_FILE + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, PROMPTS_FILE)
            except Exception:
                pass

    def get_data(self):
        with PROMPTS_LOCK:
            return {
                "captured": dict(self._data["captured"]),
                "custom": dict(self._data["custom"]),
                "enabled": dict(self._data["enabled"]),
                "counts": {
                    k: {
                        "captured_len": len(self._data["captured"].get(k, "")),
                        "custom_len": len(self._data["custom"].get(k, "")),
                        "enabled": bool(self._data["enabled"].get(k, False)),
                    }
                    for k in TIER_KEYS
                }
            }

    def update_tier(self, tier, custom_text=None, enabled=None):
        if tier not in TIER_KEYS:
            return False
        with PROMPTS_LOCK:
            if custom_text is not None:
                self._data["custom"][tier] = str(custom_text)
            if enabled is not None:
                self._data["enabled"][tier] = bool(enabled)
        self.save()
        return True

    def capture(self, tier, body_json):
        """运行时抓包: 记录进入中转的原版 system 提示词"""
        if tier not in TIER_KEYS or not isinstance(body_json, dict):
            return
        sys = body_json.get("system")
        if not sys:
            return
        if isinstance(sys, list):
            txt = "\n\n".join(blk.get("text", "") for blk in sys if isinstance(blk, dict) and blk.get("text"))
        else:
            txt = str(sys)
        txt = txt.strip()
        if not txt:
            return
        if self._data["captured"].get(tier) != txt:
            with PROMPTS_LOCK:
                self._data["captured"][tier] = txt
            self.save()

    def apply_custom(self, tier, body_json):
        """若开启了自定义提示词，将修改后的提示词注入/替换到 body_json 中，并保留 cache_control"""
        if tier not in TIER_KEYS or not isinstance(body_json, dict):
            return body_json, False
        if not self._data["enabled"].get(tier):
            return body_json, False
        custom_txt = (self._data["custom"].get(tier) or "").strip()
        if not custom_txt:
            return body_json, False

        orig_sys = body_json.get("system")
        nb = dict(body_json)
        if isinstance(orig_sys, list) and orig_sys:
            cc = None
            for blk in orig_sys:
                if isinstance(blk, dict) and blk.get("cache_control"):
                    cc = blk["cache_control"]
                    break
            new_blk = {"type": "text", "text": custom_txt}
            if cc:
                new_blk["cache_control"] = cc
            nb["system"] = [new_blk]
        else:
            nb["system"] = custom_txt
        return nb, True


_PROMPT_MGR = PromptManager()


def pick_route(conf, method, path, headers, body_json, body_raw):
    """统一路由: 返回 (upstream_name, map_model|None, reason)
    router.route = "hybrid" | "codex" | "deepseek"
    router.model = 当 route 为 codex/deepseek 时的指定模型(可空=用默认)
    """
    router = conf.get("router") or {}
    route = (router.get("route") or "hybrid").strip()
    forced = (router.get("model") or "").strip()
    raw_model = ""
    if isinstance(body_json, dict):
        raw_model = body_json.get("model") or ""
    # 剥离 [1m] 等后缀; 若基名是中转占位名(本地中转等), 视为未指定模型
    model, _suffix = _strip_model_suffix(raw_model)
    if not model or model in ("本地中转", "relay", "local"):
        model = ""

    if route == "codex":
        m = forced if forced in CODEX_MODELS else DEFAULT_CODEX_MAP
        return "codex", m, "route:codex"
    if route == "deepseek":
        return "deepseek", (forced or None), "route:deepseek"

    # hybrid: 高价值档(plan) -> 高价值模型; 其余按档位 -> 各自模型; 上游随所选模型决定
    hv = (router.get("hybrid_codex_model") or router.get("model") or "").strip() or DEFAULT_CODEX_MAP
    dd = (router.get("hybrid_deepseek_model") or "").strip() or DEFAULT_DS_MAP
    # 四档位模型(hybrid 下)
    tier = router.get("tiers") or {}
    def _up(m):
        return "codex" if m and m.startswith("gpt-") else "deepseek"
    # OPUS 档(plan/复杂推理) -> 高价值模型
    if model in ("OPUS_MODEL", "claude-opus-5", "claude-opus-4-6"):
        m = (tier.get("opus") or hv).strip()
        return _up(m), m, "hybrid:opus"
    # SONNET 档(子代理) -> 可单独配
    if model in ("SONNET_MODEL", "claude-sonnet-5"):
        m = (tier.get("sonnet") or dd).strip()
        return _up(m), m, "hybrid:sonnet"
    # FAST 档(后台小调用)
    if model in ("FAST_MODEL", "claude-haiku"):
        m = (tier.get("fast") or dd).strip()
        return _up(m), m, "hybrid:fast"
    if model in CODEX_MODELS:
        return "codex", model, "hybrid:gpt-direct"
    # 子代理档(Claude Agent SDK, model=本地中转) -> 单独分流, 不并入主模型档
    if not model and _is_subagent(headers, body_json):
        m = (tier.get("agent") or dd).strip()
        return _up(m), m, "hybrid:agent"
    # 主模型档 / 其余
    m = (tier.get("main") or dd).strip()
    return _up(m), m, "hybrid:main"


def _resp_model(resp_body):
    """从响应体解析上游真实模型名"""
    if not resp_body:
        return None
    try:
        s = resp_body
        # streaming 以 'data: ' 行开头
        if s.lstrip().startswith("event:") or "\ndata:" in s[:200] or s.lstrip().startswith("data:"):
            for line in s.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        j = json.loads(line[5:].strip())
                        m = (j.get("message") or {}).get("model") or j.get("model")
                        if m:
                            return m
                    except Exception:
                        pass
            # fallback: regex
            import re as _re
            mm = _re.search(r'"model"\s*:\s*"([^"]+)"', s)
            return mm.group(1) if mm else None
        j = json.loads(s)
        return j.get("model")
    except Exception:
        import re as _re
        mm = _re.search(r'"model"\s*:\s*"([^"]+)"', resp_body)
        return mm.group(1) if mm else None


def _iter_records_tail(max_records=400, max_bytes=16_000_000):
    """从文件末尾向前读, 拿够 max_records 条或到 max_bytes 为止(避免单条巨大导致读不到)"""
    if not os.path.exists(RECORDS):
        return []
    size = os.path.getsize(RECORDS)
    chunk = 2_000_000
    buf = b""
    pos = size
    while pos > 0 and buf.count(b"\n") <= max_records and len(buf) < max_bytes:
        read = min(chunk, pos)
        pos -= read
        with open(RECORDS, "rb") as f:
            f.seek(pos)
            buf = f.read(read) + buf
    lines = buf.split(b"\n")
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln.decode("utf-8", "replace")))
        except Exception:
            pass
    return out[-max_records:]


def stats_snapshot():
    """给 UI 的实时流量(从文件尾读足够条数, 适配超大单条记录)"""
    recs = _iter_records_tail()
    by = {}
    for r in recs:
        real_model = _resp_model(r.get('resp_body')) or r.get('sent_model') or r.get('orig_model')
        key = (r.get('route'), real_model)
        b = by.setdefault(key, {'route': r.get('route'), 'model': real_model,
                                'sent': r.get('sent_model'), 'req': 0, 'ok': 0, 'err': 0, 'last': 0})
        b['req'] += 1
        st = r.get('resp_status') or 0
        if st and st < 400:
            b['ok'] += 1
        else:
            b['err'] += 1
        b['last'] = r.get('idx', 0)
    rows = sorted(by.values(), key=lambda x: -x['last'])
    conf = load_conf()
    rt = conf.get('router') or {}
    lm = live_models(conf)
    route = rt.get('route', 'hybrid')
    # 兼容旧 UI 字段名
    mode_map = {'hybrid': 'hybrid', 'deepseek': 'deepseek', 'codex': 'codex'}
    return {'route': route, 'mode': route,
            'model': rt.get('model', ''),
            'models_ds': lm['ds'], 'models_codex': lm['cx'],
            # 旧 UI 兼容别名
            'deepseek_models': lm['ds'], 'codex_models': lm['cx'],
            'all_models': lm['ds'] + lm['cx'],
            'settings_model': rt.get('model', '') or '(自动)',
            'env_base': 'http://127.0.0.1:8400',
            'env_model': rt.get('model', '') or route,
            'codex_default_model': 'gpt-5.6-sol',
            'hybrid_codex_model': (rt.get('hybrid_codex_model') or rt.get('model') or '').strip() or 'gpt-5.6-sol',
            'hybrid_deepseek_model': (rt.get('hybrid_deepseek_model') or '').strip() or 'deepseek-flash',
            # 五档位模型(hybrid): main/opus/sonnet/fast/agent(子代理)
            'tiers': {
                'main':   (rt.get('tiers') or {}).get('main')   or (rt.get('hybrid_deepseek_model') or 'deepseek-flash'),
                'opus':   (rt.get('tiers') or {}).get('opus')   or (rt.get('hybrid_codex_model') or 'gpt-5.6-sol'),
                'sonnet': (rt.get('tiers') or {}).get('sonnet') or (rt.get('hybrid_deepseek_model') or 'deepseek-flash'),
                'fast':   (rt.get('tiers') or {}).get('fast')   or (rt.get('hybrid_deepseek_model') or 'deepseek-flash'),
                'agent':  (rt.get('tiers') or {}).get('agent')  or (rt.get('hybrid_deepseek_model') or 'deepseek-flash'),
            },
            # 环境变量名与档位对应(供 UI 展示)
            'tier_env': {
                'main': 'ANTHROPIC_MODEL',
                'opus': 'ANTHROPIC_DEFAULT_OPUS_MODEL',
                'sonnet': 'ANTHROPIC_DEFAULT_SONNET_MODEL',
                'fast': 'ANTHROPIC_SMALL_FAST_MODEL',
                'agent': 'ANTHROPIC_MODEL·plan代理',
            },
            # 推理强度(预留字段, UI 可展示/回传); 每档可独立配置 tier_efforts
            'efforts': list(EFFORT_VALUES),
            'effort': (rt.get('effort') or 'medium'),
            # 各档指纹清理开关(五张档位卡右上角)
            'strip_cc_banner': _strip_flags(rt),
            'modifier_mode': (rt.get('modifier_mode') or conf.get('modifier_mode') or 'custom').strip().lower(),
            'modifier_file_exists': os.path.exists(MODIFIER_FILE),
            'modifier_error': _MOD_MGR._err,
            'tier_efforts': {
                'main':   ((rt.get('tier_efforts') or {}).get('main')   or rt.get('effort') or 'medium'),
                'opus':   ((rt.get('tier_efforts') or {}).get('opus')   or rt.get('effort') or 'medium'),
                'sonnet': ((rt.get('tier_efforts') or {}).get('sonnet') or rt.get('effort') or 'medium'),
                'fast':   ((rt.get('tier_efforts') or {}).get('fast')   or rt.get('effort') or 'medium'),
                'agent':  ((rt.get('tier_efforts') or {}).get('agent')  or rt.get('effort') or 'medium'),
            },
            'deepseek_profile': {'default_model': 'deepseek-flash'},
            'hybrid_pins': {'ANTHROPIC_DEFAULT_OPUS_MODEL': 'gpt-5.6-sol'},
            'proxy_running': codex_up(),
            'rows': rows, 'total': len(recs),
            'codex_up': codex_up(), 'last_up': _UP['last'], 'last_model': _UP['last_model']}


# ---------- HTTP ----------

class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cc-relay/2.0"

    def log_message(self, *a):
        pass

    def _do(self, method):
        # 每次请求重读 config -> UI 改 mode 即时生效
        try:
            conf = self.server.reload_conf()
            self.server.conf = conf
        except Exception:
            conf = self.server.conf
        # /v1/models 探测: 聚合双上游模型列表(供 CC 识别)
        if self.path.split("?")[0] == "/v1/models" and method == "GET":
            names = []
            for n, u in (conf.get("upstreams") or {}).items():
                pass
            names = (["deepseek-flash", "deepseek-v4-pro",
                      "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-6-astra", "gpt-5.5",
                      "claude-opus-5", "claude-sonnet-5"])
            body = json.dumps({"data": [{"id": m, "object": "model", "owned_by": "relay"} for m in names],
                               "object": "list"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        path = self.path

        body_json = None
        ctype = self.headers.get("Content-Type", "")
        if "json" in ctype and raw:
            try:
                body_json = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                body_json = None

        up_name, map_model, reason = pick_route(conf, method, path, self.headers, body_json, raw)

        # 若路由到 codex, 确保代理已起
        if up_name == "codex" and not codex_up():
            codex_start(conf)

        up = (conf.get("upstreams") or {}).get(up_name) or {}
        base = up.get("base")
        upstream = base.rstrip("/") + path

        # model 改写
        orig_model = (body_json or {}).get("model") if isinstance(body_json, dict) else None
        sent_model = orig_model
        if map_model and isinstance(body_json, dict) and body_json.get("model") != map_model:
            body_json = dict(body_json); body_json["model"] = map_model
            sent_model = map_model
            raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")

        # 推理强度注入: effort(全局, 可按档位覆盖) -> body.thinking
        router_cfg = conf.get("router") or {}
        tier_hit = _tier_from_reason(reason)   # 档位命中, 强度与去指纹共用
        effort = (router_cfg.get("effort") or "medium").strip()
        te = router_cfg.get("tier_efforts") or {}
        if tier_hit:
            effort = (te.get(tier_hit) or effort)
        if effort in REASONING_MAP and isinstance(body_json, dict):
            body_json = dict(body_json)
            body_json["thinking"] = REASONING_MAP[effort]
            raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")

        # 1. 运行时抓包: 记录进入中转的原版 system 提示词
        if tier_hit and isinstance(body_json, dict):
            _PROMPT_MGR.capture(tier_hit, body_json)

        # 2. 若当前档位开启了在线自定义提示词，用前端配置的内容替换 (并保留 prompt-cache)
        custom_prompt_applied = False
        if tier_hit and isinstance(body_json, dict):
            body_json, custom_prompt_applied = _PROMPT_MGR.apply_custom(tier_hit, body_json)
            if custom_prompt_applied:
                raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")

        # 请求头基础过滤与鉴权注入
        fwd = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ("host", "content-length", "connection", "authorization", "x-api-key", "accept-encoding"):
                continue
            fwd[k] = v
        key = conf.get(up.get("key_env") or "", "") or ""
        if key:
            fwd["x-api-key"] = key
            fwd["authorization"] = "Bearer " + key
        fwd["Accept-Encoding"] = "identity"

        # 请求修改与指纹处理 (支持 custom / builtin / original 三种模式)
        mod_mode = (router_cfg.get("modifier_mode") or conf.get("modifier_mode") or "custom").strip().lower()
        tier_strip_on = bool(tier_hit and _strip_flags(router_cfg).get(tier_hit, False))
        stripped_n = 0

        # 构建上下文供修改器使用
        mod_ctx = {
            "upstream_name": up_name,
            "tier": tier_hit,
            "orig_model": orig_model,
            "target_model": sent_model,
            "path": path,
            "method": method,
            "is_subagent": _is_subagent(self.headers, body_json),
            "tier_strip_on": tier_strip_on,
            "modifier_mode": mod_mode,
        }

        if mod_mode == "original":
            # 【原版模式】: 不对 body 和 headers 进行任何修改或指纹过滤
            stripped_n = 0
        elif mod_mode == "builtin":
            # 【内置清理模式】: 原作者的 _strip_cc_fingerprint 逻辑
            if tier_strip_on and isinstance(body_json, dict):
                body_json, stripped_n = _strip_cc_fingerprint(body_json)
                if stripped_n:
                    raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")
        else:
            # 【自定义模式】(默认): 从 custom_modifier.py 热重载执行
            mod = _MOD_MGR.get_modifier()
            if mod:
                # 1. 执行自定义请求体修改
                try:
                    if hasattr(mod, "modify_body"):
                        nb_json, nb_raw, sn = mod.modify_body(body_json, raw, mod_ctx)
                        if nb_raw is not None:
                            raw = nb_raw
                            if nb_json is not None:
                                body_json = nb_json
                        elif nb_json is not None:
                            body_json = nb_json
                            raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")
                        stripped_n = int(sn or 0)
                except Exception as e:
                    print(f"[WARN] custom_modifier.modify_body 执行失败: {e}", file=sys.stderr)
                    if tier_strip_on and isinstance(body_json, dict):
                        body_json, stripped_n = _strip_cc_fingerprint(body_json)
                        if stripped_n:
                            raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")

                # 2. 执行自定义请求头修改
                try:
                    if hasattr(mod, "modify_headers"):
                        new_fwd = mod.modify_headers(fwd, mod_ctx)
                        if isinstance(new_fwd, dict):
                            fwd = new_fwd
                except Exception as e:
                    print(f"[WARN] custom_modifier.modify_headers 执行失败: {e}", file=sys.stderr)
            else:
                # 若无自定义文件或加载失败，安全回退到内置规则
                if tier_strip_on and isinstance(body_json, dict):
                    body_json, stripped_n = _strip_cc_fingerprint(body_json)
                    if stripped_n:
                        raw = json.dumps(body_json, ensure_ascii=False).encode("utf-8")

        data = raw if raw else None

        # 转发
        status = 502
        rbody = b""
        rheaders = {}
        err = None
        try:
            req = urllib.request.Request(upstream, data=data, method=method)
            for k, v in fwd.items():
                req.add_header(k, v)
            if up.get("proxy_url") and up["proxy_url"] != "direct":
                op = urllib.request.build_opener(urllib.request.ProxyHandler(
                    {"http": up["proxy_url"], "https": up["proxy_url"]}))
            elif conf.get("proxy_url") and conf["proxy_url"] != "direct" and up.get("proxy_url") == "direct":
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            else:
                op = urllib.request.build_opener()
            resp = op.open(req, timeout=900)
            rbody = resp.read(); status = resp.status; rheaders = dict(resp.headers)
        except urllib.error.HTTPError as e:
            rbody = e.read(); status = e.code; rheaders = dict(e.headers) if e.headers else {}
        except Exception as e:
            err = repr(e)
            rbody = json.dumps({"error": {"type": "relay_error", "message": err}}).encode()
            rheaders = {"Content-Type": "application/json"}

        # 记录
        try:
            record({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "method": method, "path": path,
                "client": self.client_address[0],
                "headers": {k: v for k, v in self.headers.items()},
                "body": body_json, "body_raw": raw.decode("utf-8", "replace")[:conf.get("max_body_capture", 2000000)],
                "route": up_name, "route_reason": reason,
                "orig_model": orig_model, "sent_model": sent_model,
                "stripped_banner": stripped_n,
                "custom_prompt": custom_prompt_applied,
                "upstream": upstream, "resp_status": status,
                "resp_body": rbody.decode("utf-8", "replace")[:conf.get("max_body_capture", 2000000)],
                "resp_error": err,
            })
        except Exception:
            pass

        _UP["last"] = up_name; _UP["last_model"] = sent_model or ""; _UP["last_ts"] = time.time()

        # 回写
        try:
            self.send_response(status)
            self.send_header("Content-Type", rheaders.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(rbody)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(rbody)
        except Exception:
            pass

    def do_GET(self): self._do("GET")
    def do_POST(self): self._do("POST")
    def do_HEAD(self): self._do("HEAD")
    def do_DELETE(self): self._do("DELETE")


class UIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _calls(self):
        """最近 N 条调用摘要(抓包查看器): CC发了什么 / 我们选了谁转发"""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        n = int((q.get("n") or ["60"])[0])
        n = max(1, min(n, 500))
        recs = _iter_records_tail(max_records=n, max_bytes=32_000_000)
        out = []
        for r in recs:
            b = r.get("body") or {}
            h = {k.lower(): v for k, v in (r.get("headers") or {}).items()}
            out.append({
                "idx": r.get("idx"),
                "ts": r.get("ts"),
                "path": r.get("path"),
                "orig_model": r.get("orig_model"),
                "sent_model": r.get("sent_model"),
                "route": r.get("route"),
                "route_reason": r.get("route_reason"),
                "status": r.get("resp_status"),
                "agent_id": (h.get("x-claude-code-agent-id") or "")[:12],
                "session": (h.get("x-claude-code-session-id") or "")[:8],
                "msgs": len(b.get("messages") or []),
                "tools": len(b.get("tools") or []),
                "stream": b.get("stream"),
            })
        return {"calls": out, "total": len(out)}

    def _call_detail(self):
        """单条完整内容(headers/body/响应)"""
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        idx = (q.get("idx") or [None])[0]
        if idx is None:
            return {"error": "missing idx"}
        for r in _iter_records_tail(max_records=500, max_bytes=20_000_000):
            if str(r.get("idx")) == str(idx):
                return r
        return {"error": "not found"}

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self._json(stats_snapshot())
        elif self.path.startswith("/api/prompts"):
            self._json(_PROMPT_MGR.get_data())
        elif self.path.startswith("/api/calls"):
            self._json(self._calls())
        elif self.path.startswith("/api/call"):
            self._json(self._call_detail())
        elif self.path in ("/", "/index.html"):
            try:
                b = open(os.path.join(BASE, "ui.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
            except Exception:
                self._json({"error": "ui missing"}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(ln).decode() if ln else "{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        p = self.path.split("?")[0]
        if p == "/api/route":
            route = data.get("route")
            if route not in ("hybrid", "codex", "deepseek"):
                self._json({"error": "bad route"}, 400); return
            with CONF_LOCK:                      # 读-改-写整体串行, 免五档连点丢更新
                conf = load_conf()
                rt = conf.setdefault("router", {})
                rt["route"] = route
                # 各档指纹清理开关 (与路由档位无关, 任何模式下都可改; 按档合并更新)
                if "strip_cc_banner" in data:
                    cur = _strip_flags(rt)           # 先归一化, 兼容旧布尔配置
                    payload = data["strip_cc_banner"]
                    if isinstance(payload, bool):
                        cur["main"] = payload        # 旧客户端: 旧按钮本来就只代表主档
                    elif isinstance(payload, dict):
                        for k in TIER_KEYS:
                            if k not in payload:
                                continue
                            if not isinstance(payload[k], bool):
                                self._json({"error": "strip_cc_banner.%s must be boolean" % k}, 400)
                                return
                            cur[k] = payload[k]
                    else:
                        self._json({"error": "strip_cc_banner must be boolean or object"}, 400)
                        return
                    rt["strip_cc_banner"] = cur
                # 修改器模式: custom (默认自定义脚本) | builtin (内置清理) | original (原版纯透传)
                if "modifier_mode" in data:
                    m = str(data["modifier_mode"]).strip().lower()
                    if m in ("custom", "builtin", "original"):
                        rt["modifier_mode"] = m
                # 推理强度 effort 为全局设置, 任何路由下都可修改 (档位见 EFFORT_VALUES)
                if data.get("effort") is not None:
                    v = str(data.get("effort") or "").strip()
                    if v in EFFORT_VALUES:
                        rt["effort"] = v
                    else:
                        rt.pop("effort", None)
                # 每档独立推理强度 tier_efforts (main/opus/sonnet/fast/agent), 合并更新
                if data.get("tier_efforts") is not None:
                    t = data.get("tier_efforts") or {}
                    cur = dict(rt.get("tier_efforts") or {})
                    for k in ("main", "opus", "sonnet", "fast", "agent"):
                        v = str((t or {}).get(k) or "").strip()
                        if v in EFFORT_VALUES:
                            cur[k] = v
                    if cur:
                        rt["tier_efforts"] = cur
                    else:
                        rt.pop("tier_efforts", None)
                if route == "hybrid":
                    # hybrid 五档: tiers.main/opus/sonnet/fast/agent (各自模型, 上游随模型名决定)
                    if data.get("tiers") is not None:
                        t = data.get("tiers") or {}
                        cur = dict(rt.get("tiers") or {})
                        for k in ("main", "opus", "sonnet", "fast", "agent"):
                            v = str((t or {}).get(k) or "").strip()
                            if v:
                                cur[k] = v
                        if cur:
                            rt["tiers"] = cur
                        else:
                            rt.pop("tiers", None)
                    # 兼容旧字段
                    if data.get("codex_model") is not None:
                        v = str(data.get("codex_model") or "").strip()
                        if v: rt["hybrid_codex_model"] = v
                        else: rt.pop("hybrid_codex_model", None)
                    if data.get("deepseek_model") is not None:
                        v = str(data.get("deepseek_model") or "").strip()
                        if v: rt["hybrid_deepseek_model"] = v
                        else: rt.pop("hybrid_deepseek_model", None)
                    rt.pop("model", None)
                else:
                    m = (data.get("model") or "").strip()
                    if not m:
                        rt.pop("model", None)
                    else:
                        rt["model"] = m
                _save_conf(conf)
                self._json({"ok": True, "route": route, "model": rt.get("model", "")})
        elif p == "/api/proxy":
            act = data.get("action")
            if act == "start": self._json({"result": codex_start(load_conf())})
            elif act == "stop": self._json({"result": codex_stop()})
            else: self._json({"error": "bad action"}, 400)
        elif p == "/api/reset":
            try:
                open(RECORDS, "w").close()
            except Exception:
                pass
            self._json({"ok": True})
        elif p == "/api/prompts":
            tier = data.get("tier")
            custom_txt = data.get("custom")
            enabled = data.get("enabled")
            if tier in TIER_KEYS:
                _PROMPT_MGR.update_tier(tier, custom_text=custom_txt, enabled=enabled)
                self._json({"ok": True, "tier": tier})
            else:
                self._json({"error": "invalid tier"}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204); self.end_headers()


def serve_ui(conf):
    host = conf.get("listen_host", "127.0.0.1")
    port = conf.get("ui_port", 8610)
    srv = ThreadingHTTPServer((host, port), UIHandler)
    print(f"cc-relay UI on http://{host}:{port}")
    srv.serve_forever()


def serve(a):
    conf = load_conf()
    host = conf.get("listen_host", "127.0.0.1")
    port = conf.get("listen_port", 8400)
    srv = ThreadingHTTPServer((host, port), Relay)
    srv.reload_conf = lambda: load_conf()
    # 同时起 UI (可选)
    if not a.no_ui:
        t = threading.Thread(target=serve_ui, args=(conf,), daemon=True)
        t.start()
    srv.conf = conf
    print(f"cc-relay v2 on http://{host}:{port}")
    for n, u in (conf.get("upstreams") or {}).items():
        print(f"  [{n}] -> {u.get('base')}")
    print(f"  records: {RECORDS}")
    print(f"  CC: ANTHROPIC_BASE_URL=http://{host}:{port}  ANTHROPIC_AUTH_TOKEN={conf.get('fake_api_key')}")
    srv.serve_forever()


def stats(a):
    recs = read_records()
    print(f"总记录: {len(recs)}")
    by_route = {}
    by_model = {}
    for r in recs:
        by_route[r.get("route", "?")] = by_route.get(r.get("route", "?"), 0) + 1
        by_model[r.get("orig_model", "?")] = by_model.get(r.get("orig_model", "?"), 0) + 1
    print("路由分布:", by_route)
    print("原始 model 分布:", by_model)
    print("\n最近 12 条:")
    for r in recs[-12:]:
        print(f"  #{r.get('idx')} {r.get('ts')} {r.get('orig_model')} -> {r.get('sent_model')} "
              f"[{r.get('route')}] {r.get('path')[:24]} {r.get('resp_status')}")


def last(a):
    n = a.n or 5
    for r in read_records()[-n:]:
        b = r.get("body") or {}
        print("=" * 70)
        print(f"#{r.get('idx')} {r.get('ts')} {r.get('method')} {r.get('path')} -> {r.get('resp_status')}")
        print(f"route={r.get('route')} ({r.get('route_reason')}) | model {r.get('orig_model')} -> {r.get('sent_model')}")
        h = r.get("headers", {})
        print("agent-id:", h.get("x-claude-code-agent-id"), "| session:", (h.get("X-Claude-Code-Session-Id") or "")[:8])
        print("msgs:", len(b.get("messages") or []), "tools:", len(b.get("tools") or []))


def dump(a):
    recs = read_records()
    if a.which == "all":
        for r in recs[-20:]:
            print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
    else:
        for r in recs:
            if str(r.get("idx")) == a.which:
                print(json.dumps(r, ensure_ascii=False, indent=1)); return
        print("未找到 #%s" % a.which)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve").add_argument("--no-ui", action="store_true")
    sub.add_parser("ui")
    sub.add_parser("stats")
    l = sub.add_parser("last"); l.add_argument("n", type=int, nargs="?", default=5)
    d = sub.add_parser("dump"); d.add_argument("which")
    sub.add_parser("startproxy")
    sub.add_parser("stopproxy")
    sub.add_parser("proxycheck")
    a = ap.parse_args()
    os.makedirs(BASE, exist_ok=True)
    if a.cmd == "serve": serve(a)
    elif a.cmd == "ui": serve_ui(load_conf())
    elif a.cmd == "stats": stats(a)
    elif a.cmd == "last": last(a)
    elif a.cmd == "dump": dump(a)
    elif a.cmd == "startproxy": print(codex_start(load_conf()))
    elif a.cmd == "stopproxy": print(codex_stop())
    elif a.cmd == "proxycheck": print("up" if codex_up() else "down")
    else: ap.print_help()
