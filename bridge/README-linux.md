# Dotii 管理中心 · Linux（Ubuntu）

同一套 Python 后台，换成 Linux 平台的适配层：**开机自启走 systemd user 单元**，
不再需要托盘宿主；其余功能（网页管理台、USB/Wi-Fi/蓝牙、表情与固件烧录）完全一致。

## 环境要求

- Ubuntu 22.04 / 24.04（其他 systemd 发行版同理），Python 3.9+
- `sudo apt install -y python3 python3-venv`
- 可选：`sudo apt install -y ffmpeg`（Bambu 摄像头转码需要）

## 安装（推荐）

```bash
cd /path/to/Dotii-Display
bash bridge/install-linux.sh            # 默认端口 8787
bash bridge/install-linux.sh --port 9000
```

脚本只做四件事：建 `.venv-linux` 虚拟环境 → 装 `bridge/requirements.txt` →
写 `~/.config/systemd/user/dotii-bridge.service` → `enable --now` 并启动。

装完打开 <http://127.0.0.1:8787>。

## 前台调试

```bash
bash bridge/run-linux.sh               # 自动准备 venv，Ctrl+C 退出
bash bridge/run-linux.sh --port 9000
```

WSL、容器等没有 systemd 的环境就用这个。

## 服务管理

```bash
systemctl --user status dotii-bridge      # 状态
journalctl --user -u dotii-bridge -f      # 日志
systemctl --user restart dotii-bridge     # 重启
bash bridge/install-linux.sh --uninstall  # 停止并移除服务（保留数据）
```

网页里的「开机自启」开关在 Linux 上映射到 `systemctl --user enable/disable`。
想让服务在**未登录桌面**时也自启（开机即跑）：`sudo loginctl enable-linger $USER`。

## 权限

| 用途 | 需要的权限 |
|---|---|
| USB 数据通道 / 烧录 | 用户在 `dialout` 组：`sudo usermod -aG dialout $USER`，注销重登生效 |
| 蓝牙扫描配网 | BlueZ 正常运行；Ubuntu 桌面默认可用，服务器版需 `sudo systemctl enable --now bluetooth` |

## 数据位置

| 内容 | 路径 |
|---|---|
| 运行数据、配置、状态 | `~/.local/share/dotii/`（或 `$XDG_DATA_HOME/dotii`） |
| 日志 | `journalctl --user -u dotii-bridge` |
| 服务单元 | `~/.config/systemd/user/dotii-bridge.service` |
| 虚拟环境 | `<仓库>/.venv-linux/` |

设置 `DOTII_RUNTIME_DIR=/abs/path` 可把数据目录指到别处（测试用）。

## 与 Windows 版的差异

- **没有托盘宿主**：`bridge_app.py` 是 Windows 专用（Win32 托盘 + Job 对象），Linux 直接由 systemd 托管进程。
- **没有打包产物**：Windows 是 PyInstaller 单文件；Linux 走 venv + 源码，升级就是 `git pull`。
- **开机自启**：Windows 写注册表 Run 键；Linux 写 systemd user 单元。
- **外部工具**：Windows 用 `tools/node/node.exe`、`tools/ffmpeg/bin/ffmpeg.exe`；Linux 优先系统 `node` / `ffmpeg`（`/usr/bin`、`/usr/local/bin`、`/snap/bin`），也认 `tools/` 下的同构目录。

## 常见问题

**端口被占用**：`ss -lntp | grep 8787`，换端口用 `--port`。

**设备端连不上**：确认 `ufw` 放行 8787（`sudo ufw allow 8787/tcp`），设备与电脑在同一网段。

**串口看不到设备**：`ls -l /dev/ttyACM*`；没有的话检查数据线（不是充电线）和 `dialout` 组。

**烧录失败**：先 `sudo usermod -aG dialout $USER` 重登，或临时 `sudo chmod 666 /dev/ttyACM0`。
