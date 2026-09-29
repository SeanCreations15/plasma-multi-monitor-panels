from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from panel_sync.events import (
    PLASMA_CONFIG_NAME,
    _EVENT_HEADER,
    _IN_Q_OVERFLOW,
    InotifyEventSource,
    plasma_config_path,
)


class ConfigPathTests(unittest.TestCase):
    def test_absolute_xdg_config_home_is_used(self) -> None:
        with patch.dict(
            os.environ,
            {"XDG_CONFIG_HOME": "/tmp/custom-config"},
            clear=False,
        ):
            self.assertEqual(
                plasma_config_path(),
                Path("/tmp/custom-config") / PLASMA_CONFIG_NAME,
            )

    def test_relative_xdg_config_home_is_ignored(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": "relative-config"},
                clear=False,
            ),
            patch(
                "panel_sync.events.Path.home",
                return_value=Path("/home/test"),
            ),
        ):
            self.assertEqual(
                plasma_config_path(),
                Path("/home/test/.config") / PLASMA_CONFIG_NAME,
            )

    def test_home_config_is_the_default(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "panel_sync.events.Path.home",
                return_value=Path("/home/test"),
            ),
        ):
            self.assertEqual(
                plasma_config_path(),
                Path("/home/test/.config") / PLASMA_CONFIG_NAME,
            )


@unittest.skipUnless(sys.platform.startswith("linux"), "inotify needs Linux")
class InotifyEventSourceTests(unittest.TestCase):
    def test_direct_write_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / PLASMA_CONFIG_NAME
            with InotifyEventSource(target) as source:
                target.write_text("launchers=a\n", encoding="utf-8")
                self.assertTrue(source.wait(1.0))

    def test_unrelated_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / PLASMA_CONFIG_NAME
            with InotifyEventSource(target) as source:
                (Path(directory) / "other.conf").write_text(
                    "value\n",
                    encoding="utf-8",
                )
                self.assertFalse(source.wait(0.05))

    def test_atomic_replacement_keeps_directory_watch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / PLASMA_CONFIG_NAME
            target.write_text("old\n", encoding="utf-8")
            with InotifyEventSource(target) as source:
                replacement = root / "replacement"
                replacement.write_text("new\n", encoding="utf-8")
                os.replace(replacement, target)
                self.assertTrue(source.wait(1.0))

                target.write_text("newer\n", encoding="utf-8")
                self.assertTrue(source.wait(1.0))

    def test_deletion_and_recreation_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / PLASMA_CONFIG_NAME
            target.write_text("old\n", encoding="utf-8")
            with InotifyEventSource(target) as source:
                target.unlink()
                self.assertTrue(source.wait(1.0))

                target.write_text("new\n", encoding="utf-8")
                self.assertTrue(source.wait(1.0))

    def test_queue_overflow_requests_a_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / PLASMA_CONFIG_NAME
            with InotifyEventSource(target) as source:
                event = _EVENT_HEADER.pack(-1, _IN_Q_OVERFLOW, 0, 0)
                self.assertTrue(source._read_events(event))

    def test_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / PLASMA_CONFIG_NAME
            source = InotifyEventSource(target)
            source.close()
            source.close()
            self.assertTrue(source.closed)
            with self.assertRaises(OSError):
                source.wait(0)

    def test_missing_directory_is_a_setup_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "missing" / PLASMA_CONFIG_NAME
            with self.assertRaises(OSError):
                InotifyEventSource(target)


if __name__ == "__main__":
    unittest.main()
