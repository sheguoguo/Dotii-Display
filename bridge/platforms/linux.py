"""Linux (Ubuntu 等 systemd 发行版) implementation of management-center services.

与 macOS 版保持一致的分工：平台适配器只负责可写目录、开机自启、外部工具查找
和串口枚举，业务逻辑仍然留在 codex_bridge.py 里。

开机自启走 **systemd user 单元**（`dotii-bridge.service`），不再使用桌面自启动项：
服务在无图形会话（SSH / 开机未登录）时也能起来，网页里的"开机自启"开关直接映射
到 `systemctl --user enable/disable`。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from .base import RunCommand


STARTUP_UNIT = "dotii-bridge.service"
DOTII_USB_VID = 0x303A
DOTII_USB_PID = 0x1001
PORT_PATTERN = re.compile(r"^/dev/(ttyUSB|ttyACM)[0-9]{1,3}$")
BY_ID_PATTERN = re.compile(r"^/dev/serial/by-id/[A-Za-z0-9._:+-]{1,200}$")
ENABLED_STATES = {"enabled", "enabled-runtime", "static", "indirect", "generated"}


def parse_serial_ports(ports: Iterable[Any]) -> list[dict[str, object]]:
    """Normalize pyserial port records, Dotii 设备排在最前面。"""
    output: list[dict[str, object]] = []
    for item in ports:
        port = str(getattr(item, "device", "") or "")
        if not (PORT_PATTERN.fullmatch(port) or BY_ID_PATTERN.fullmatch(port)):
            continue
        vid = getattr(item, "vid", None)
        pid = getattr(item, "pid", None)
        dotii = vid == DOTII_USB_VID and pid == DOTII_USB_PID
        description = str(getattr(item, "description", "") or port)
        identity = ""
        if isinstance(vid, int) and isinstance(pid, int):
            identity = f"USB VID:PID={vid:04X}:{pid:04X}"
        output.append({
            "port": port,
            "name": description[:120],
            "pnp_id": identity,
            "dotii": dotii,
        })
    output.sort(key=lambda entry: (not bool(entry["dotii"]), str(entry["port"]).lower()))
    return output


class LinuxPlatformAdapter:
    name = "linux"
    # BlueZ + bleak 在 Ubuntu 上可用；配对交给系统蓝牙代理，不由我们主动触发。
    bluetooth_available = True
    bluetooth_pair_on_connect = False
    serial_flash_available = True
    # 打开数据通道时先拉高 DTR，避免 DTR=0/RTS=1 把 USB Serial/JTAG 复位。
    serial_data_dtr_on_open = True

    def runtime_folder(self) -> Path:
        testing_override = os.environ.get("DOTII_RUNTIME_DIR")
        if testing_override and Path(testing_override).is_absolute():
            return Path(testing_override)
        data_home = os.environ.get("XDG_DATA_HOME")
        base = Path(data_home) if data_home and Path(data_home).is_absolute() \
            else Path.home() / ".local" / "share"
        return base / "dotii"

    def hidden_creation_flags(self) -> int:
        return 0

    # ---- 开机自启：systemd user 单元 -------------------------------------

    def _user_unit_path(self) -> Path:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        base = Path(config_home) if config_home and Path(config_home).is_absolute() \
            else Path.home() / ".config"
        return base / "systemd" / "user" / STARTUP_UNIT

    @property
    def startup_available(self) -> bool:
        return shutil.which("systemctl") is not None

    def _systemctl(self, *arguments: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["systemctl", "--user", *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None

    def startup_command(self, python_executable: Path, app_script: Path) -> str:
        return f'"{python_executable}" -B "{app_script}" --app-server'

    def packaged_startup_command(self, management_center: Path) -> str:
        # Linux 没有独立宿主程序；开机自启由 systemd 单元承担。
        return self.current_startup_command(
            frozen=True,
            executable=management_center,
            app_script=management_center,
            management_center=management_center,
        )

    def current_startup_command(
        self,
        *,
        frozen: bool,
        executable: Path,
        app_script: Path,
        management_center: Path,
    ) -> str:
        del frozen, executable, app_script, management_center
        return str(self._user_unit_path())

    def startup_enabled(self) -> bool:
        if shutil.which("systemctl") is None:
            return False
        result = self._systemctl("is-enabled", STARTUP_UNIT)
        if result is None or result.returncode != 0:
            return False
        return result.stdout.strip() in ENABLED_STATES

    def set_startup(self, enabled: bool, command: str) -> bool:
        del command  # 命令写在单元文件里，运行时字符串不再参与
        if shutil.which("systemctl") is None:
            raise OSError("当前系统没有 systemd，无法设置开机自启")
        if enabled and not self._user_unit_path().is_file():
            raise OSError("服务单元尚未安装，请先运行 bridge/install-linux.sh")
        # 只 enable/disable，不带 --now：当前进程就在这个服务里，停掉会掐断请求。
        result = self._systemctl("enable" if enabled else "disable", STARTUP_UNIT)
        if result is None:
            raise OSError("无法执行 systemctl --user")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200] or "systemctl 返回失败"
            raise OSError(f"无法更新开机自启：{detail}")
        return self.startup_enabled()

    # ---- 外部工具查找 ----------------------------------------------------

    def ffmpeg_candidates(self, runtime_folder: Path, tools_root: Path) -> list[Path]:
        return [
            runtime_folder / "tools" / "ffmpeg" / "bin" / "ffmpeg",
            tools_root / "ffmpeg" / "bin" / "ffmpeg",
            Path("/usr/bin/ffmpeg"),
            Path("/usr/local/bin/ffmpeg"),
            Path("/snap/bin/ffmpeg"),
        ]

    def codex_cli_candidates(self, runtime_folder: Path, application_root: Path) -> list[Path]:
        return [
            runtime_folder / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            application_root / "tools" / "codex-cli" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            application_root / "tools" / "codex" / "bin" / "codex",
            Path.home() / ".npm-global" / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
            Path.home() / ".npm-global" / "bin" / "codex",
            Path.home() / ".local" / "bin" / "codex",
            Path("/usr/local/bin/codex"),
            Path("/usr/bin/codex"),
        ]

    def bundled_node_candidates(self, application_root: Path) -> list[Path]:
        return [
            application_root / "tools" / "node" / "bin" / "node",
            Path.home() / ".local" / "bin" / "node",
            Path("/usr/local/bin/node"),
            Path("/usr/bin/node"),
        ]

    def esptool_python_candidates(self) -> list[Path]:
        output = [Path(sys.executable)]
        configured = os.environ.get("IDF_PYTHON_ENV_PATH")
        if configured:
            output.append(Path(configured) / "bin" / "python")
        root = Path(os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif"))) / "python_env"
        if root.is_dir():
            output.extend(sorted(root.glob("*/bin/python"), reverse=True))
        return output

    # ---- USB 串口 --------------------------------------------------------

    def scan_serial_ports(self, run: RunCommand) -> list[dict[str, object]]:
        del run
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        try:
            return parse_serial_ports(list_ports.comports())
        except (OSError, TypeError, ValueError):
            return []

    def valid_serial_port(self, port: str) -> bool:
        return bool(PORT_PATTERN.fullmatch(port) or BY_ID_PATTERN.fullmatch(port))
