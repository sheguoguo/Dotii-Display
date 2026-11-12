from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from platforms.linux import (  # noqa: E402
    LinuxPlatformAdapter,
    STARTUP_UNIT,
    parse_serial_ports,
)


class LinuxPlatformAdapterTests(unittest.TestCase):
    def test_capabilities_match_desktop_expectations(self) -> None:
        adapter = LinuxPlatformAdapter()
        self.assertEqual(adapter.name, "linux")
        self.assertTrue(adapter.bluetooth_available)
        self.assertFalse(adapter.bluetooth_pair_on_connect)
        self.assertTrue(adapter.serial_flash_available)
        self.assertTrue(adapter.serial_data_dtr_on_open)
        self.assertEqual(adapter.hidden_creation_flags(), 0)

    def test_runtime_folder_follows_xdg_data_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_home = Path(temporary) / "xdg-data"
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": str(data_home)}, clear=False):
                self.assertEqual(LinuxPlatformAdapter().runtime_folder(), data_home / "dotii")

    def test_runtime_folder_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "dotii-run"
            with mock.patch.dict("os.environ", {"DOTII_RUNTIME_DIR": str(override)}, clear=False):
                self.assertEqual(LinuxPlatformAdapter().runtime_folder(), override)

    def test_runtime_folder_ignores_relative_override(self) -> None:
        with mock.patch.dict("os.environ", {"DOTII_RUNTIME_DIR": "relative/path"}, clear=False):
            with mock.patch.dict("os.environ", {"XDG_DATA_HOME": ""}, clear=False):
                self.assertEqual(
                    LinuxPlatformAdapter().runtime_folder(),
                    Path.home() / ".local" / "share" / "dotii",
                )

    def test_external_tools_prefer_unix_binaries(self) -> None:
        adapter = LinuxPlatformAdapter()
        self.assertEqual(adapter.ffmpeg_candidates(Path("/runtime"), Path("/tools"))[0].name, "ffmpeg")
        self.assertEqual(adapter.bundled_node_candidates(Path("/app"))[0].name, "node")
        self.assertTrue(
            all(candidate.suffix != ".exe"
                for candidate in adapter.codex_cli_candidates(Path("/runtime"), Path("/app")))
        )

    def test_serial_port_validation_rejects_windows_and_legacy_names(self) -> None:
        adapter = LinuxPlatformAdapter()
        self.assertTrue(adapter.valid_serial_port("/dev/ttyACM0"))
        self.assertTrue(adapter.valid_serial_port("/dev/ttyUSB3"))
        self.assertFalse(adapter.valid_serial_port("COM13"))
        self.assertFalse(adapter.valid_serial_port("/dev/ttyS0"))
        self.assertFalse(adapter.valid_serial_port("/dev/ttyACM0/../../etc/passwd"))

    def test_serial_scan_sorts_dotii_first(self) -> None:
        ports = parse_serial_ports([
            SimpleNamespace(device="/dev/ttyACM1", description="Other", vid=0x1234, pid=0x5678),
            SimpleNamespace(device="/dev/ttyACM0", description="Dotii", vid=0x303A, pid=0x1001),
        ])
        self.assertEqual([item["port"] for item in ports], ["/dev/ttyACM0", "/dev/ttyACM1"])
        self.assertTrue(ports[0]["dotii"])

    def test_startup_maps_to_systemd_user_unit(self) -> None:
        adapter = LinuxPlatformAdapter()
        with mock.patch("platforms.linux.shutil.which", return_value=None):
            self.assertFalse(adapter.startup_available)
            self.assertFalse(adapter.startup_enabled())
            with self.assertRaises(OSError):
                adapter.set_startup(True, "")

    def test_startup_requires_installed_unit(self) -> None:
        adapter = LinuxPlatformAdapter()
        with mock.patch("platforms.linux.shutil.which", return_value="/usr/bin/systemctl"):
            with mock.patch.object(adapter, "_user_unit_path", return_value=Path("/nonexistent/unit")):
                with self.assertRaises(OSError):
                    adapter.set_startup(True, "")
        self.assertEqual(STARTUP_UNIT, "dotii-bridge.service")


if __name__ == "__main__":
    unittest.main()
