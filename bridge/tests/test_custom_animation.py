from __future__ import annotations

import sys
import unittest
import zlib
from pathlib import Path

from dotii_test_support import temporary_directory

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from codex_bridge import (  # noqa: E402
    CUSTOM_ANIMATION_MAX_BYTES,
    CustomAssetStore,
)


GIF_HEADER = b"GIF89a"


class CustomAnimationStoreTests(unittest.TestCase):
    def store(self) -> CustomAssetStore:
        temporary = temporary_directory()
        self.addCleanup(temporary.cleanup)
        return CustomAssetStore(Path(temporary.name))

    def test_animation_starts_unavailable(self) -> None:
        store = self.store()

        self.assertEqual(
            store.animation_info(),
            {"animation_available": False, "animation_revision": 0, "animation_size": 0},
        )

    def test_write_animation_reports_revision_and_size(self) -> None:
        store = self.store()
        body = GIF_HEADER + b"\x01\x00\x01\x00" + b"\x00" * 32

        info = store.write_animation(body)

        self.assertTrue(info["animation_available"])
        self.assertEqual(info["animation_size"], len(body))
        self.assertEqual(info["animation_revision"], zlib.crc32(body) & 0xFFFFFFFF)
        self.assertEqual(store.read_animation(), body)

    def test_write_animation_rejects_non_gif_payload(self) -> None:
        store = self.store()

        with self.assertRaises(ValueError):
            store.write_animation(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        self.assertFalse(store.animation_info()["animation_available"])

    def test_write_animation_rejects_payload_over_limit(self) -> None:
        store = self.store()

        with self.assertRaises(ValueError):
            store.write_animation(GIF_HEADER + b"\x00" * (CUSTOM_ANIMATION_MAX_BYTES + 1))

    def test_source_upload_routes_gif_to_animation(self) -> None:
        store = self.store()
        body = GIF_HEADER + b"\x00" * 16

        info = store.write_source(body, "image/gif")

        self.assertTrue(info["animation_available"])
        self.assertEqual(store.read_animation(), body)

    def test_clear_animation_removes_stored_gif(self) -> None:
        store = self.store()
        store.write_animation(GIF_HEADER + b"\x00" * 16)

        info = store.clear_animation()

        self.assertFalse(info["animation_available"])
        with self.assertRaises(FileNotFoundError):
            store.read_animation()


if __name__ == "__main__":
    unittest.main()
