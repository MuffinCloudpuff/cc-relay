# cc-relay Deployment and Installation Guide

> 🌐 English | [简体中文](INSTALL.md)

This guide walks through a complete `cc-relay` setup from scratch: installation, cross-platform configuration, hooking up the three upstreams, injecting the Claude Code configuration, and troubleshooting common failures.

---

## 📋 Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Getting the Source and Initial Configuration](#2-getting-the-source-and-initial-configuration)
3. [Wiring Up the Three Upstreams](#3-wiring-up-the-three-upstreams)
   - [3.1 DeepSeek Direct](#31-deepseek-direct)
   - [3.2 Codex (CLIProxyAPI) Bridge](#32-codex-cliproxyapi-bridge)
   - [3.3 Gemini (Antigravity Tools) Bridge](#33-gemini-antigravity-tools-bridge)
4. [Injecting the Claude Code Configuration](#4-injecting-the-claude-code-configuration)
5. [Seamless Automation on Windows (Wrapper & Lifecycle)](#5-seamless-automation-on-windows-wrapper--lifecycle)
6. [Daemonization on macOS / Linux](#6-daemonization-on-macos--linux)
7. [Daily Operations and Diagnostics](#7-daily-operations-and-diagnostics)
8. [Troubleshooting FAQ](#8-troubleshooting-faq)

---

## 1. Prerequisites

- **Python 3.8 or newer** (must be on your system `PATH`)
  - Check with: `python --version` or `python3 --version`
  - *Note: cc-relay is built purely on the standard library — no pip packages are required.*
- **Claude Code CLI** (installed and working)
  - Check with: `claude --version`
- **Git**
  - Check with: `git --version`

---

## 2. Getting the Source and Initial Configuration

### 2.1 Clone the repository

```bash
git clone https://github.com/xsneser/cc-relay.git
cd cc-relay
```

### 2.2 Create your local config file

```bash
# Windows (CMD / PowerShell)
copy config.example.json config.json

# macOS / Linux
cp config.example.json config.json
```

> ⚠️ **Security warning**: `config.json` holds your real upstream API keys. Never upload or commit it to a public repository. The bundled `.gitignore` already excludes it.

---

## 3. Wiring Up the Three Upstreams

### 3.1 DeepSeek Direct

1. Get an API key from the [DeepSeek open platform](https://platform.deepseek.com/).
2. Open `config.json` and fill in `real_deepseek_key`:
   ```json
   {
     "real_deepseek_key": "sk-you…-key"
   }
   ```
3. With the default configuration the DeepSeek endpoint is `https://api.deepseek.com/anthropic` and works over a direct connection.

---

### 3.2 Codex (CLIProxyAPI) Bridge

[CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI) converts OpenAI Codex / GPT models into the Anthropic Messages format.

1. **Download and place it**:
   - Download the binary for your platform from the CLIProxyAPI release page.
   - On Windows, put `cli-proxy-api.exe` into this project's `codex-proxy/` directory.
2. **Create and configure `config.yaml`**:
   - Copy the template:
     ```bash
     cd codex-proxy
     copy config.example.yaml config.yaml
     ```
   - Edit `codex-proxy/config.yaml` and set the listen port (default `8317`) and proxy as needed.
3. **Complete the OAuth / token login**:
   - Run `start_proxy.bat`, or launch `cli-proxy-api.exe` manually, to authorise the account.
   - Verify the service responds at `http://127.0.0.1:8317/v1/models`.
4. **Bind it into `config.json`**:
   ```json
   {
     "codex_proxy_key": "your-proxy-key",
     "codex_exe": "C:\\path\\to\\cc-relay\\codex-proxy\\cli-proxy-api.exe",
     "codex_config": "C:\\path\\to\\cc-relay\\codex-proxy\\config.yaml"
   }
   ```
   *Once `codex_exe` is configured, cc-relay will silently start the proxy in the background whenever a request routes to Codex and the proxy is not running.*

---

### 3.3 Gemini (Antigravity Tools) Bridge

1. Make sure the local Antigravity Tools is running and listening on port `8045` (it exposes an Anthropic-compatible endpoint).
2. Configure it in `config.json`:
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
3. Verify connectivity: click the **Gemini probe** in the web dashboard, or call `GET /api/probe?name=antigravity`.

---

## 4. Injecting the Claude Code Configuration

`cc-relay` can merge the relay routing environment variables into your global Claude config file `~/.claude/settings.json` in one step.

### Method A: automated injection script (recommended)

```bash
# preview what would be written (safe, read-only)
python apply_settings.py --dry-run

# perform the safe incremental merge
python apply_settings.py
```

`apply_settings.py` reads the current `config.json` for the listen port and `fake_api_key`, then merges the following variables into the `env` node of `settings.json` — **without ever losing or overwriting your other custom environment variables**:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8400",
    "ANTHROPIC_AUTH_TOKEN": "***",
    "ANTHROPIC_MODEL": "relay-main[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "OPUS_MODEL[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "SONNET_MODEL[1m]",
    "ANTHROPIC_SMALL_FAST_MODEL": "FAST_MODEL[1m]"
  }
}
```

### Method B: per-terminal environment variables

If you would rather not touch the global config file, inject them per terminal:

**Windows PowerShell:**
```powershell
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:8400"
$env:ANTHROPIC_AUTH_TOKEN="***"
$env:ANTHROPIC_MODEL="relay-main[1m]"
$env:ANTHROPIC_DEFAULT_OPUS_MODEL="OPUS_MODEL[1m]"
$env:ANTHROPIC_DEFAULT_SONNET_MODEL="SONNET_MODEL[1m]"
$env:ANTHROPIC_SMALL_FAST_MODEL="FAST_MODEL[1m]"
claude
```

**macOS / Linux Bash / Zsh:**
```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8400"
export ANTHROPIC_AUTH_TOKEN="***"
export ANTHROPIC_MODEL="relay-main[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="OPUS_MODEL[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="SONNET_MODEL[1m]"
export ANTHROPIC_SMALL_FAST_MODEL="FAST_MODEL[1m]"
claude
```

---

## 5. Seamless Automation on Windows (Wrapper & Lifecycle)

To keep the experience friction-free, the project ships a Windows wrapper and background automation:

### 5.1 Make the wrapper take priority

1. Add `C:\path\to\cc-relay\wrapper` to the system `PATH` **ahead of** the npm global directory.
2. From then on, typing `claude` in any console:
   - calls `wrapper/claude.cmd` first;
   - runs `python lifecycle.py autostart` to wake the relay in under a second;
   - starts the background `lifecycle.py watch` watchdog;
   - then hands over to the real Claude Code.
3. Once every `claude.exe` process has exited, the watchdog notices the idle timeout and stops the relay and the Codex upstream, freeing system resources.

### 5.2 Desktop shortcut

Double-click `start_desktop.bat` to bring the services up and open the web dashboard (`http://127.0.0.1:8610`) in your default browser.

---

## 6. Daemonization on macOS / Linux

On POSIX systems you can run `cc-relay` as a persistent per-user background service with `systemd` (Linux) or `launchd` (macOS).

### 6.1 Linux systemd user service

Create `~/.config/systemd/user/cc-relay.service`:

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

Enable and start it:
```bash
systemctl --user daemon-reload
systemctl --user enable --now cc-relay.service
systemctl --user status cc-relay.service
```

---

## 7. Daily Operations and Diagnostics

| Goal | Command |
| :--- | :--- |
| **Foreground debug run** | `python cc_relay.py serve` |
| **Foreground run without the UI** | `python cc_relay.py serve --no-ui` |
| **Check service and process status** | `python lifecycle.py status` |
| **Stop the relay and all sidecars** | `python lifecycle.py stopall` |
| **Inspect the last 10 captured calls** | `python cc_relay.py last 10` |
| **Traffic statistics** | `python cc_relay.py stats` |
| **Dry-run the config injection** | `python apply_settings.py --dry-run` |

---

## 8. Troubleshooting FAQ

### Q1: `[Errno 10048]` / `Address already in use` on startup
- **Cause**: port 8400 (relay) or 8610 (UI) is taken by a leftover process or another application.
- **Fix**:
  - On Windows run `python lifecycle.py stopall` to force-clean leftovers.
  - Or change `"listen_port"` and `"ui_port"` in `config.json` to free ports, then re-run `python apply_settings.py`.

### Q2: `401 Unauthorized` or `invalid api key`
- **Cause**: the token Claude Code sends does not match `fake_api_key` in `config.json`.
- **Fix**:
  - Check `"fake_api_key"` in `config.json` (default `sk-rel…0000`).
  - Re-run `python apply_settings.py` to refresh `~/.claude/settings.json`.

### Q3: `502 Bad Gateway` after switching to the Codex upstream
- **Cause**: `codex-proxy` (CLIProxyAPI) is not running, or its OAuth login has expired.
- **Fix**:
  - Check that `http://127.0.0.1:8317/v1/models` is reachable.
  - Run `codex-proxy/start_proxy.bat` manually and look for login or proxy errors.

### Q4: `503 No available accounts` after switching to the Gemini upstream
- **Cause**: this error comes straight from the local Antigravity Tools endpoint (8045), meaning the currently bound Google account is out of quota or has become invalid.
- **Fix**: open the Antigravity Tools client and refresh or re-login the Google account.

### Q5: Why is the prompt-cache hit rate so low in the web UI?
- **Cause**:
  1. the upstream model does not enable or support prompt caching;
  2. the System prompt or conversation history changed drastically between two consecutive requests;
  3. note: `cc-relay`'s `builtin` trimming mode carries cache breakpoints forward, so it does not break cache continuity.
