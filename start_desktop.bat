@echo off
rem ============================================================
rem  Claude 图形客户端启动器 (带 cc-relay 自动拉起与环境注入)
rem  双击此脚本即可:
rem    1. 自动在后台静默拉起 cc-relay (8400) 与 UI (8610)
rem    2. 临时为客户端注入 ANTHROPIC_* 环境变量
rem    3. 启动图形客户端
rem ============================================================
setlocal
cd /d "%~dp0"

echo [1/3] 正在启动后台中转服务...
pythonw lifecycle.py autostart

echo [2/3] 正在配置本地代理环境变量...
set "ANTHROPIC_BASE_URL=http://127.0.0.1:8400"
set "ANTHROPIC_AUTH_TOKEN=sk-relay-local-0000"
set "ANTHROPIC_MODEL=relay-main[1m]"
set "ANTHROPIC_DEFAULT_OPUS_MODEL=OPUS_MODEL[1m]"
set "ANTHROPIC_DEFAULT_SONNET_MODEL=SONNET_MODEL[1m]"
set "ANTHROPIC_SMALL_FAST_MODEL=FAST_MODEL[1m]"

echo [3/3] 正在拉起图形客户端...

:: 检测官方 Claude Desktop 常见安装路径
if exist "%LOCALAPPDATA%\Programs\Claude\Claude.exe" (
    start "" "%LOCALAPPDATA%\Programs\Claude\Claude.exe" %*
    goto done
)

if exist "%ProgramFiles%\Claude\Claude.exe" (
    start "" "%ProgramFiles%\Claude\Claude.exe" %*
    goto done
)

:: 检测用户自定义路径或已加入 PATH 的 claude
where claude.exe >nul 2>&1
if %errorlevel%==0 (
    start "" claude.exe %*
    goto done
)

echo.
echo ============================================================
echo [提示] cc-relay 代理已在后台就绪 (http://127.0.0.1:8400)
echo 未检测到自动安装的 Claude.exe，已为你打开 Web 监控面板。
echo 你可以直接打开你的图形客户端，它已可以使用本地代理！
echo ============================================================
start "" "http://127.0.0.1:8610"

:done
exit /b 0
