#!/usr/bin/env bash
# Dotii 管理中心 · Linux (Ubuntu) 安装脚本
#
#   bash bridge/install-linux.sh              # 安装并启动（默认端口 8787）
#   bash bridge/install-linux.sh --port 9000  # 换端口
#   bash bridge/install-linux.sh --uninstall  # 停止并卸载服务（保留数据）
#
# 只做四件事：建 venv、装依赖、写 systemd user 单元、enable 并启动。
# 数据目录：$XDG_DATA_HOME/dotii 或 ~/.local/share/dotii
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$BRIDGE_DIR")"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${DOTII_VENV:-$ROOT_DIR/.venv-linux}"
PORT="${DOTII_PORT:-8787}"
UNIT_NAME="dotii-bridge.service"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

say() { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf '\n错误：%s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) fail "未知参数：$1" ;;
  esac
done

# ---- 卸载 ---------------------------------------------------------------
if [ "${UNINSTALL:-0}" = "1" ]; then
  step "卸载 Dotii 管理中心服务"
  systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  rm -f "$UNIT_PATH"
  systemctl --user daemon-reload 2>/dev/null || true
  say "已移除 $UNIT_NAME"
  say "虚拟环境与数据目录保留：$VENV_DIR / $(dirname "$VENV_DIR")"
  exit 0
fi

# ---- 前置检查 -----------------------------------------------------------
step "检查运行环境"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || \
  fail "找不到 $PYTHON_BIN。先执行：sudo apt install -y python3 python3-venv"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || \
  fail "需要 Python 3.9 或更高版本"
command -v systemctl >/dev/null 2>&1 || \
  fail "没有 systemd，本脚本无法安装为服务（WSL / 容器请直接用 bridge/run-linux.sh 前台运行）"
say "python：$("$PYTHON_BIN" -V 2>&1)"
say "项目目录：$ROOT_DIR"
say "端口：$PORT"

# ---- 虚拟环境 -----------------------------------------------------------
step "准备虚拟环境"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR" || \
    fail "创建 venv 失败。先执行：sudo apt install -y python3-venv"
  say "已创建 $VENV_DIR"
else
  say "复用已有 $VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
say "安装依赖（bleak / Pillow / pyserial / esptool）"
"$VENV_DIR/bin/python" -m pip install -r "$BRIDGE_DIR/requirements.txt" --quiet

# ---- 服务单元 -----------------------------------------------------------
step "安装 systemd user 服务"
mkdir -p "$UNIT_DIR"
sed -e "s|@ROOT@|$ROOT_DIR|g" \
    -e "s|@BRIDGE@|$BRIDGE_DIR|g" \
    -e "s|@VENV@|$VENV_DIR|g" \
    -e "s|@PORT@|$PORT|g" \
    "$BRIDGE_DIR/dotii-bridge.service.in" > "$UNIT_PATH"
say "已写入 $UNIT_PATH"

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
sleep 2
systemctl --user --no-pager --lines=0 status "$UNIT_NAME" | head -6 || true

# ---- 权限提示 -----------------------------------------------------------
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx dialout; then
  step "串口权限"
  say "当前用户不在 dialout 组，USB 烧录与数据通道会失败："
  say "  sudo usermod -aG dialout $USER   然后注销重新登录"
fi

step "完成"
say "管理台： http://127.0.0.1:$PORT"
say "设备端： http://<本机局域网IP>:$PORT/api/v1/snapshot"
say "日志：   journalctl --user -u $UNIT_NAME -f"
if command -v loginctl >/dev/null 2>&1; then
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
    say ""
    say "想让它在开机后、未登录桌面时也自动运行："
    say "  sudo loginctl enable-linger $USER"
  fi
fi
