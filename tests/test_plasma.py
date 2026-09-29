from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from panel_sync.plasma import (
    QUERY_SCRIPT,
    PlasmaClient,
    find_kbuildsycoca,
    find_qdbus,
)
from panel_sync.state import empty_state


class StubClient(PlasmaClient):
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.qdbus = "/usr/bin/qdbus6"
        self.timeout = 8
        self.scripts: list[str] = []
        self.refreshes = 0

    def evaluate(self, script: str) -> str:
        self.scripts.append(script)
        return json.dumps(self.payload)

    def refresh_launcher_model(self) -> None:
        self.refreshes += 1


class DiscoveryTests(unittest.TestCase):
    def test_both_standard_task_manager_plugins_are_queried(self) -> None:
        self.assertIn("org.kde.plasma.icontasks", QUERY_SCRIPT)
        self.assertIn("org.kde.plasma.taskmanager", QUERY_SCRIPT)
        self.assertNotIn("dmsManaged", QUERY_SCRIPT)

    def test_records_are_sorted_by_screen_panel_and_widget(self) -> None:
        records = [
            {
                "panel": 20,
                "screen": 1,
                "widget": 5,
                "plugin": "org.kde.plasma.taskmanager",
                "root": "b",
                "general": "b",
            },
            {
                "panel": 10,
                "screen": 0,
                "widget": 3,
                "plugin": "org.kde.plasma.icontasks",
                "root": "a",
                "general": "a",
            },
        ]
        managers = StubClient(records).task_managers(empty_state())
        self.assertEqual([item.widget for item in managers], [3, 5])

    def test_invalid_record_is_rejected(self) -> None:
        records = [{"panel": 1}]
        with self.assertRaises(ValueError):
            StubClient(records).task_managers(empty_state())

    def test_unsupported_plugin_is_rejected(self) -> None:
        records = [
            {
                "panel": 1,
                "screen": 0,
                "widget": 2,
                "plugin": "example.unsupported",
                "root": "",
                "general": "",
            }
        ]
        with self.assertRaises(ValueError):
            StubClient(records).task_managers(empty_state())

    def test_set_all_writes_both_plasma_keys(self) -> None:
        client = StubClient([])
        client.set_all_launchers("a,b")
        script = client.scripts[-1]
        self.assertIn('currentConfigGroup = []', script)
        self.assertIn('currentConfigGroup = ["General"]', script)
        self.assertIn('var target = ["a", "b"]', script)
        self.assertEqual(client.refreshes, 1)


class QdbusTests(unittest.TestCase):
    def test_explicit_command_must_exist(self) -> None:
        with patch("panel_sync.plasma.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                find_qdbus("missing-qdbus")

    def test_qdbus6_is_preferred(self) -> None:
        with patch(
            "panel_sync.plasma.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}" if name == "qdbus6" else None,
        ):
            self.assertEqual(find_qdbus(), "/usr/bin/qdbus6")


class LauncherRefreshTests(unittest.TestCase):
    def test_kbuildsycoca6_is_required(self) -> None:
        with patch("panel_sync.plasma.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                find_kbuildsycoca()

    def test_full_cache_rebuild_triggers_launcher_refresh(self) -> None:
        client = PlasmaClient.__new__(PlasmaClient)
        client.kbuildsycoca = "/usr/bin/kbuildsycoca6"
        with patch("panel_sync.plasma.subprocess.run") as run:
            client.refresh_launcher_model()
        run.assert_called_once_with(
            ["/usr/bin/kbuildsycoca6", "--noincremental"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
