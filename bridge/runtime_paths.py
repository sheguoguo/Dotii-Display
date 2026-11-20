"""Locate application resources in source and packaged builds."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from platforms import current_platform

# 源码运行时把状态/日志/配置放在工作空间里，避开用户 AppData。
STATE_DISPLAY_HOME_ENV = "STATE_DISPLAY_HOME"
WORKSPACE_RUNTIME_DIRNAME = ".state-display"


def is_frozen() -> bool:
    """Return whether the bridge is running from a PyInstaller executable."""
    return bool(getattr(sys, "frozen", False))


def macos_app_contents(executable: Path | None = None) -> Path | None:
    """Return the enclosing ``.app/Contents`` directory when one exists."""
    candidate = (executable or Path(sys.executable)).resolve()
    for parent in candidate.parents:
        if parent.name != "Contents":
            continue
        if parent.parent.suffix.lower() == ".app":
            return parent
    return None


def application_resources_root() -> Path:
    """Return persistent read-only resources shipped with the application."""
    if is_frozen() and sys.platform == "darwin":
        contents = macos_app_contents()
        if contents is not None:
            return contents / "Resources"
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def auxiliary_executables_root() -> Path:
    """Return the directory containing packaged helper executables."""
    if is_frozen() and sys.platform == "darwin":
        contents = macos_app_contents()
        if contents is not None:
            return contents / "MacOS"
    return Path(sys.executable).resolve().parent if is_frozen() else Path(__file__).resolve().parent


def bundle_root() -> Path:
    """Return the read-only root containing bundled data files."""
    if is_frozen():
        extracted = getattr(sys, "_MEIPASS", None)
        if extracted:
            return Path(extracted).resolve()
        return application_resources_root()
    return Path(__file__).resolve().parent


def application_root() -> Path:
    """Return the directory used as the process working root.

    源码仓库布局是 ``<repo>/bridge/*.py``，根目录是 ``<repo>``；服务器上是扁平部署
    ``/home/ss/dotii-bridge/*.py``，根目录就是脚本目录。用 ``CMakeLists.txt`` 判断，
    别无条件取 ``parent.parent``——扁平部署下那会跑到 ``/home/ss``。
    """
    if is_frozen():
        return application_resources_root()
    here = script_root()
    if here.name == "bridge" and (here.parent / "CMakeLists.txt").is_file():
        return here.parent
    return here


def tools_root() -> Path:
    """Return the optional application-private external tools directory."""
    return application_root() / "tools"


def runtime_folder() -> Path:
    """Return the writable application-data directory.

    源码运行时默认落在**工程工作空间**里（``<workspace>/.state-display``），
    不写用户的 AppData：这套后台经常被脚本/CI/沙箱起起来，那些身份往往没有
    系统目录的写权限；放在工程目录里权限永远跟着工程走。
    打包版仍然用平台标准的用户数据目录，装完即用。
    """
    override = os.environ.get(STATE_DISPLAY_HOME_ENV)
    if override:
        return Path(override).expanduser()
    if not is_frozen():
        folder = workspace_root() / WORKSPACE_RUNTIME_DIRNAME
        migrate_legacy_runtime(folder)
        return folder
    return current_platform().runtime_folder()


def script_root() -> Path:
    """Return the directory that actually holds these bridge scripts."""
    return Path(__file__).resolve().parent


def workspace_root() -> Path:
    """Return the directory that holds the runtime data folder.

    源码仓库布局是 ``<workspace>/Dotii-Display/bridge/*.py``：脚本在 ``bridge/`` 里，
    运行时目录要落到仓库外面（``<workspace>/.state-display``），免得进 git。
    服务器上是扁平部署（``/home/ss/dotii-bridge/*.py``，没有 ``bridge/`` 子目录），
    这种就直接放在脚本同级。用脚本自身位置判断，别用 application_root()——
    它假设了 ``bridge/`` 这一层，扁平部署下会跑到 /home/ss 去。
    """
    here = script_root()
    if here.name == "bridge" and (here / "codex_bridge.py").is_file():
        return here.parent.parent
    return here


def legacy_runtime_folder() -> Path:
    """Return the historical per-user data directory (read-only fallback)."""
    return current_platform().runtime_folder()


def migrate_legacy_runtime(target: Path) -> bool:
    """Copy an existing AppData runtime into the workspace once.

    只搬一次：目标目录已经有内容就跳过。目的是保住 ``bridge-token`` 和用户配置，
    否则设备要重新配网、表情和显示设置全部丢掉。
    """
    marker = target / ".migrated-from-appdata"
    if marker.exists():
        return False
    legacy = legacy_runtime_folder()
    try:
        if target.resolve() == legacy.resolve():
            return False
    except OSError:
        return False
    if not legacy.is_dir():
        return False
    try:
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()) and not marker.exists():
            # 目标里已经有东西（用户手动放过），不覆盖，只记标记。
            marker.write_text("skipped: target not empty\n", encoding="utf-8")
            return False
        for item in legacy.iterdir():
            if item.is_dir():
                shutil.copytree(item, target / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target / item.name)
        marker.write_text(f"copied from {legacy}\n", encoding="utf-8")
        return True
    except OSError:
        return False


def project_root() -> Path:
    """Return the source or packaged root containing firmware build files."""
    return bundle_root() if is_frozen() else application_root()


def project_version(root: Path | None = None) -> str:
    """Read the shared firmware and management-center version from CMake."""
    try:
        cmake = ((root or project_root()) / "CMakeLists.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'^\s*set\s*\(\s*PROJECT_VER\s+"?([^"\s\)]+)', cmake, re.MULTILINE)
    return match.group(1)[:48] if match else ""


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)


def sibling_executable(name: str) -> Path:
    """Return a packaged sibling executable, or its source-tree equivalent."""
    if is_frozen():
        return auxiliary_executables_root() / name
    return bundle_root() / name
