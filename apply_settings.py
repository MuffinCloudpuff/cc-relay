#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
安全注入 cc-relay 环境变量至 ~/.claude/settings.json
- 读取本项目的 config.json 获取动态端口与 fake_api_key
- 增量合并 settings.json 的 env 节点，绝不覆盖用户的其他已有环境变量与配置
- 支持 --dry-run 预览修改
"""
import os
import sys
import json
import argparse

BASE = os.environ.get("CC_RELAY_DIR") or os.path.dirname(os.path.abspath(__file__))


def load_relay_conf(conf_path=None):
    path = conf_path or os.path.join(BASE, "config.json")
    if not os.path.exists(path):
        path = os.path.join(BASE, "config.example.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def is_loopback_host(host):
    h = str(host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1", "ip6-localhost")


def _sanitize_dict_for_preview(val):
    if isinstance(val, dict):
        out = {}
        sensitive_substrings = ("key", "token", "secret", "password", "credential")
        for k, v in val.items():
            lower_k = str(k).lower()
            if any(s in lower_k for s in sensitive_substrings):
                out[k] = "<redacted>" if v else ""
            else:
                out[k] = _sanitize_dict_for_preview(v)
        return out
    elif isinstance(val, list):
        return [_sanitize_dict_for_preview(x) for x in val]
    return val


def main():
    parser = argparse.ArgumentParser(description="Inject cc-relay settings into ~/.claude/settings.json")
    parser.add_argument("--config", default=None, help="Path to cc-relay config.json")
    parser.add_argument("--settings", default=None, help="Path to ~/.claude/settings.json")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without modifying file")
    args = parser.parse_args()

    conf = load_relay_conf(args.config)
    host = conf.get("listen_host", "127.0.0.1")
    if not is_loopback_host(host):
        print(f"[ERROR] 拒绝将非本地回环地址 ({host}) 注入 Claude 配置，保护本地安全。")
        sys.exit(1)
    port = conf.get("listen_port", 8400)
    fake_key = conf.get("fake_api_key", "sk-relay-local-0000")

    settings_path = args.settings or os.path.expanduser(r"~/.claude/settings.json")
    os.makedirs(os.path.dirname(os.path.abspath(settings_path)), exist_ok=True)

    data = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
        except Exception as e:
            print(f"[WARN] 无法读取已有配置文件 ({e})，将创建新配置字典")
            data = {}

    env_node = data.setdefault("env", {})
    if not isinstance(env_node, dict):
        env_node = {}
        data["env"] = env_node

    relay_env = {
        "ANTHROPIC_BASE_URL": f"http://{host}:{port}",
        "ANTHROPIC_AUTH_TOKEN": fake_key,
        "ANTHROPIC_MODEL": "relay-main[1m]",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "OPUS_MODEL[1m]",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "SONNET_MODEL[1m]",
        "ANTHROPIC_SMALL_FAST_MODEL": "FAST_MODEL[1m]",
    }

    env_node.update(relay_env)

    output_str = json.dumps(data, ensure_ascii=False, indent=2)

    if args.dry_run:
        print("=== [DRY RUN] 目标文件预览 ===")
        print(f"目标路径: {settings_path}")
        sanitized = _sanitize_dict_for_preview(data)
        print(json.dumps(sanitized, ensure_ascii=False, indent=2))
        return

    tmp_path = settings_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(output_str + "\n")
    os.replace(tmp_path, settings_path)

    print("=" * 64)
    print("[OK] 已成功将 cc-relay 环境变量安全增量写入 Claude 配置文件:")
    print(f"     {settings_path}")
    print(f"     中转地址: http://{host}:{port}")
    print(f"     Token:   <redacted>")
    print("     已保留所有其他既有环境变量与配置项。")
    print("=" * 64)


if __name__ == "__main__":
    main()
