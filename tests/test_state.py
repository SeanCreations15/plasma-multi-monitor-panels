from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from panel_sync.state import (
    default_state_path,
    empty_state,
    read_state,
    write_state,
)


class StateTests(unittest.TestCase):
    def test_missing_state_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(read_state(path), empty_state())

    def test_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "state.json"
            expected = {
                "version": 1,
                "selected": "a,b",
                "widgets": {},
            }
            write_state(path, expected)
            self.assertEqual(read_state(path), expected)
            self.assertFalse(list(path.parent.glob("*.tmp")))

    def test_bad_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 99, "selected": None, "widgets": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_state(path)

    def test_bad_widgets_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 1, "selected": None, "widgets": []}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_state(path)

    def test_bad_widget_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "selected": "a",
                        "widgets": {"2": {"effective": "a"}},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                read_state(path)

    def test_state_uses_the_home_directory(self) -> None:
        with patch(
            "panel_sync.state.Path.home",
            return_value=Path("/home/test"),
        ):
            self.assertEqual(
                default_state_path(),
                Path(
                    "/home/test/.local/state/"
                    "panel-sync/state.json"
                ),
            )


if __name__ == "__main__":
    unittest.main()
