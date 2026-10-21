from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inbetween import paths


class PathTests(unittest.TestCase):
    def test_source_uses_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(
            os.environ, {"LOCALAPPDATA": temp}
        ), mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(paths.data_root(), Path(temp) / "GFI")

    def test_frozen_uses_folder_next_to_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe_dir = Path(temp) / "GFI-0.1.0"
            exe_dir.mkdir()
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                sys, "executable", str(exe_dir / "gfi.exe")
            ):
                self.assertEqual(paths.data_root(), exe_dir / "gfi-data")
                self.assertEqual(paths.ensure_data_root(), exe_dir / "gfi-data")
                self.assertFalse((exe_dir / "gfi-data" / ".write-test").exists())

    def test_frozen_resources_use_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
                sys, "_MEIPASS", temp, create=True
            ):
                self.assertEqual(paths.resource_root(), Path(temp))


class FindToolTests(unittest.TestCase):
    def test_bundled_ffmpeg_wins_over_path(self) -> None:
        from inbetween import ffmpeg

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundled = root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
            bundled.parent.mkdir(parents=True)
            bundled.write_bytes(b"")
            with mock.patch.object(ffmpeg, "resource_root", return_value=root), mock.patch(
                "inbetween.ffmpeg.shutil.which", return_value=r"C:\Windows\ffmpeg.exe"
            ):
                self.assertEqual(ffmpeg.find_tool("ffmpeg"), str(bundled))


if __name__ == "__main__":
    unittest.main()
