#!/usr/bin/env bash
# Dotii 管理中心 · Linux 前台运行（调试用，不进 systemd）
#
#   bash bridge/run-linux.sh                 # 首次会自动建 venv 装依赖
#   bash bridge/run-linux.sh --port 9000
#
# 需要常驻/开机自启请用 install-linux.sh。
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$BRIDGE_DIR")"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${DOTII_VENV:-$ROOT_DIR/.venv-linux}"
PORT="${DOTII_PORT:-8787}"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf '未知参数：%s\n' "$1" >&2; exit 1 ;;
  esac
done

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "首次运行，创建虚拟环境：$VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR" || {
    echo "创建 venv 失败，先执行：sudo apt install -y python3-venv" >&2
    exit 1
  }
fi

"$VENV_DIR/bin/python" -c 'import bleak, serial, PIL' 2>/dev/null || {
  echo "安装依赖…"
  "$VENV_DIR/bin/python" -m pip install -r "$BRIDGE_DIR/requirements.txt" --quiet
}

echo "管理台： http://127.0.0.1:$PORT    （Ctrl+C 退出）"
cd "$ROOT_DIR"
exec "$VENV_DIR/bin/python" -B "$BRIDGE_DIR/codex_bridge.py" --app-server --port "$PORT"
