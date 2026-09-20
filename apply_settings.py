# -*- coding: utf-8 -*-
"""
一键将 cc-relay 环境变量配置写入全局 ~/.claude/settings.json
"""
import os, json

settings_path = os.path.expanduser(r"~\.claude\settings.json")

env_config = {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8400",
    "ANTHROPIC_AUTH_TOKEN": "sk-relay-local-0000",
    "ANTHROPIC_MODEL": "relay-main[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "OPUS_MODEL[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "SONNET_MODEL[1m]",
    "ANTHROPIC_SMALL_FAST_MODEL": "FAST_MODEL[1m]",
}

if not os.path.exists(settings_path):
    data = {"env": env_config}
else:
    with open(settings_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception:
            data = {}
    data["env"] = env_config

with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("=" * 60)
print("[OK] 已成功将中转代理环境变量写入全局配置文件:")
print(f"     {settings_path}")
print("     现在你的所有 Claude 桌面端与插件都会自动走 cc-relay (8400)！")
print("=" * 60)
