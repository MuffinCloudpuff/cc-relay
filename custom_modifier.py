# -*- coding: utf-8 -*-
"""
CC-Relay 自定义请求修改器 (Custom Request Modifier)
===================================================
本文件是 cc-relay 的用户可编辑规则文件。
当你修改并保存本文件后，cc-relay 会在下一个请求处理时【自动热重载】，无需重启中转进程。

功能覆盖:
1. modify_headers(): 自定义修改/增删发往上游的 HTTP 请求头（如伪装 User-Agent、删除特征头等）
2. modify_body(): 自定义修改/清洗请求体（如清理 CC 提示词指纹、替换 system 提示词、增删字段等）
3. 内置函数导出: 导出了作者原版的指纹清理算法 builtin_strip_cc_fingerprint，供参考或直接调用

执行模式说明 (可在 config.json 的 "modifier_mode" 中设置):
- "custom"   : 使用本文件中的 modify_headers 和 modify_body 逻辑（默认推荐）
- "builtin"  : 使用原作者写死在 cc_relay.py 里的内置清理逻辑
- "original" : 原版纯透传模式，不碰任何请求体和非必要请求头
"""

import re
from typing import Dict, Any, Tuple, Optional


# ==============================================================================
# 原版内置指纹清理算法 (供参考与在 modify_body 中直接调用)
# ==============================================================================

CC_ID_PAT = (
    r"(?:You are Claude Code,?\s*Anthropic's official CLI for Claude\.?"
    r"|You are a Claude agent,\s*built on Anthropic's Claude Agent SDK\.?)"
)
CC_BILLING_PREFIX = "x-anthropic-billing-header:"


def builtin_strip_cc_fingerprint(body: dict) -> Tuple[dict, int]:
    """
    原作者实现的 CC 指纹清理逻辑:
    - 识别并删除 system 提示词中的 CC 身份声明与 billing 指纹
    - 如果被删的块带有 cache_control，顺延给后续幸存块，保护 prompt cache 不失效
    - 返回: (修改后的 body 字典, 被清理的块数)
    """
    sysv = body.get("system")
    if sysv is None:
        return body, 0

    # 1. system 是普通字符串形式
    if isinstance(sysv, str):
        ns = re.sub(CC_ID_PAT, "", sysv)
        if ns == sysv:
            return body, 0
        nb = dict(body)
        if ns.strip():
            nb["system"] = ns
        else:
            nb.pop("system", None)
        return nb, 1

    # 2. system 不是列表则不处理
    if not isinstance(sysv, list):
        return body, 0

    kept = []
    carried = []   # (被删块本该落到的下标, cache_control)
    removed = 0

    for blk in sysv:
        txt = blk.get("text") if isinstance(blk, dict) else None
        if isinstance(txt, str):
            t = txt.strip()
            # 整块命中 billing 前缀或整块等于身份句 -> 整块移除
            if t.startswith(CC_BILLING_PREFIX) or re.fullmatch(CC_ID_PAT, t):
                removed += 1
                if blk.get("cache_control"):
                    carried.append((len(kept), blk["cache_control"]))
                continue
            # 身份句混在长文本中 -> 仅正则剥除句子
            if re.search(CC_ID_PAT, txt):
                nb_blk = dict(blk)
                nb_blk["text"] = re.sub(CC_ID_PAT, "", txt)
                removed += 1
                kept.append(nb_blk)
                continue
        kept.append(blk)

    if not removed:
        return body, 0

    # 缓存断点顺延处理
    for pos, cc in carried:
        placed = False
        for i in range(pos, len(kept)):
            blk = kept[i]
            if not isinstance(blk, dict):
                continue
            if blk.get("cache_control"):
                placed = True
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
        nb.pop("system", None)
    return nb, removed


# ==============================================================================
# 用户自定义钩子 1: 请求头修改 (Header Modification)
# ==============================================================================

def modify_headers(headers: Dict[str, str], context: Dict[str, Any]) -> Dict[str, str]:
    """
    修改发往上游模型的 HTTP Headers。

    参数:
        headers: 客户端发送并已经完成基础 hop-by-hop 过滤的请求头字典。
                 已自动注入对应上游的 x-api-key 与 authorization。
        context: 请求上下文，包含丰富信息:
            - "upstream_name" : 当前路由的目标上游 (如 "deepseek", "codex")
            - "tier"          : 命中的档位名 (如 "main", "opus", "sonnet", "fast", "agent", 或 None)
            - "orig_model"    : 客户端发来的原始模型名 (如 "本地中转[1m]")
            - "target_model"  : 最终发往上游的模型名 (如 "deepseek-flash", "gpt-5.6-sol")
            - "path"          : 请求 URL 路径 (如 "/v1/messages")
            - "method"        : 请求方法 (如 "POST")
            - "is_subagent"   : 是否为子代理 (True / False)
            - "tier_strip_on" : 当前档位在 UI 或配置中的去指纹开关是否为 True (bool)

    返回:
        修改后的 headers 字典 (键值对)
    """
    fwd = dict(headers)

    # 检查当前档位的开关是否开启 (如果开关关闭，且你想遵循开关状态，可以提前跳过)
    # 如果你想强制生效，也可以忽略 context.get("tier_strip_on")
    strip_enabled = context.get("tier_strip_on", True)

    if strip_enabled:
        # ----------------------------------------------------------------------
        # 【示例 1】删除 Claude Code 特有的追踪与指纹头
        # 常见 CC 请求头包括:
        #   x-claude-code-agent-id
        #   x-claude-code-session-id
        #   x-anthropic-billing-header (若出现在 header 中)
        # ----------------------------------------------------------------------
        headers_to_remove = [
            "x-claude-code-agent-id",
            "x-claude-code-session-id",
            "x-anthropic-billing-header",
        ]
        for h in headers_to_remove:
            # 大小写不敏感删除
            for existing_k in list(fwd.keys()):
                if existing_k.lower() == h.lower():
                    fwd.pop(existing_k, None)

        # ----------------------------------------------------------------------
        # 【示例 2】伪装 User-Agent
        # 默认 CC 发出的是类似 "claude-cli/..." 的 UA
        # 你可以按需替换为通用的 Python 或 SDK UA:
        # ----------------------------------------------------------------------
        # fwd["User-Agent"] = "anthropic-sdk-python/0.40.0"

        # ----------------------------------------------------------------------
        # 【示例 3】根据目标上游添加自定义 Header
        # ----------------------------------------------------------------------
        # if context.get("upstream_name") == "deepseek":
        #     fwd["X-Custom-Client"] = "cc-relay-custom"

    return fwd


# ==============================================================================
# 用户自定义钩子 2: 请求体修改与指纹清洗 (Body Modification)
# ==============================================================================

def modify_body(
    body_json: Optional[dict],
    body_raw: bytes,
    context: Dict[str, Any]
) -> Tuple[Optional[dict], Optional[bytes], int]:
    """
    修改发往上游模型的请求体内容。

    参数:
        body_json: 反序列化后的 JSON 字典 (若请求非 JSON 则为 None)
        body_raw : 原始请求字节流
        context  : 请求上下文信息 (包含 upstream_name, tier, orig_model, target_model, tier_strip_on 等)

    返回:
        三元组: (new_body_json, new_body_raw, stripped_count)
        - new_body_json : 修改后的 JSON 字典。如果为 None，则直接使用 new_body_raw。
        - new_body_raw  : 修改后的字节流。如果为 None，cc-relay 会自动将 new_body_json 序列化为 UTF-8 字节。
        - stripped_count: 本次修改/清理的特征数量 (用于抓包查看器和统计记录)
    """
    if not isinstance(body_json, dict):
        return body_json, body_raw, 0

    strip_enabled = context.get("tier_strip_on", True)
    if not strip_enabled:
        # 当前档位未开启修改/清理，原样返回
        return body_json, body_raw, 0

    new_body = dict(body_json)
    stripped_count = 0

    # --------------------------------------------------------------------------
    # 【步骤 1】调用内置的指纹清理逻辑 (可注释掉或自定义改写)
    # --------------------------------------------------------------------------
    new_body, stripped_count = builtin_strip_cc_fingerprint(new_body)

    # --------------------------------------------------------------------------
    # 【步骤 2】自定义额外处理示例:
    # 示例 A: 注入或替换 system 提示词
    # --------------------------------------------------------------------------
    # sys_val = new_body.get("system")
    # if isinstance(sys_val, str):
    #     new_body["system"] = sys_val + "\n\n(Respond concisely.)"
    # elif isinstance(sys_val, list):
    #     new_body["system"].append({"type": "text", "text": "(Respond concisely.)"})

    # 示例 B: 过滤敏感字段或自定义替换
    # if "custom_field" in new_body:
    #     new_body.pop("custom_field", None)

    return new_body, None, stripped_count
