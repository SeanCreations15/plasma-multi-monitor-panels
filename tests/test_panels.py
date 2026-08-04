from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from plasma_taskbar_sync.panels import panels_match, query_panels, sync_panels


def tree(value: str, *, tray_id: int | None = None) -> dict[str, object]:
    keys: dict[str, object] = {"value": value}
    if tray_id is not None:
        keys["SystrayContainmentId"] = tray_id
    return {"keys": keys, "groups": {}}


def panel(panel_id: int, screen: int, value: str = "same") -> dict[str, object]:
    return {
        "id": panel_id,
        "screen": screen,
        "properties": {"location": "bottom", "height": 30},
        "widgets": [
            {
                "type": "org.kde.plasma.systemtray",
                "index": 0,
                "config": tree(value, tray_id=panel_id + 100),
                "trayConfig": tree(value),
            }
        ],
    }


class ScriptClient:
    def __init__(self, snapshots: list[list[dict[str, object]]]) -> None:
        self.snapshots = snapshots
        self.scripts: list[str] = []

    def evaluate(self, script: str) -> str:
        self.scripts.append(script)
        if "function readTree" in script:
            return json.dumps(self.snapshots.pop(0))
        return ""


class PanelTests(unittest.TestCase):
    def test_systray_instance_id_is_not_compared(self) -> None:
        self.assertTrue(panels_match(panel(1, 0), panel(2, 1)))

    def test_configuration_difference_is_detected(self) -> None:
        self.assertFalse(panels_match(panel(1, 0), panel(2, 1, "different")))

    def test_dry_run_does_not_apply(self) -> None:
        client = ScriptClient([[panel(1, 0), panel(2, 1, "different")]])
        with tempfile.TemporaryDirectory() as directory:
            result = sync_panels(
                client, Path(directory) / "state.json", dry_run=True
            )
            self.assertTrue(result.changed)
            self.assertEqual(len(client.scripts), 1)

    def test_apply_is_verified(self) -> None:
        before = [panel(1, 0), panel(2, 1, "different")]
        after = [panel(1, 0), panel(2, 1)]
        client = ScriptClient([before, after])
        with tempfile.TemporaryDirectory() as directory:
            result = sync_panels(client, Path(directory) / "state.json")
            self.assertTrue(result.changed)
            self.assertEqual(len(client.scripts), 3)

    def test_secondary_edit_becomes_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            sync_panels(
                ScriptClient([[panel(1, 0), panel(2, 1), panel(3, 2)]]),
                state,
            )
            before = [panel(1, 0), panel(2, 1, "new"), panel(3, 2)]
            after = [panel(1, 0, "new"), panel(2, 1, "new"), panel(3, 2, "new")]
            result = sync_panels(ScriptClient([before, after]), state)
            self.assertEqual(result.source_screen, 1)
            self.assertTrue(result.changed)
            self.assertFalse(result.conflict)

    def test_third_monitor_edit_becomes_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            sync_panels(
                ScriptClient([[panel(1, 0), panel(2, 1), panel(3, 2)]]),
                state,
            )
            before = [panel(1, 0), panel(2, 1), panel(3, 2, "new")]
            after = [panel(1, 0, "new"), panel(2, 1, "new"), panel(3, 2, "new")]
            result = sync_panels(ScriptClient([before, after]), state)
            self.assertEqual(result.source_screen, 2)
            self.assertFalse(result.conflict)

    def test_matching_edits_on_two_panels_win_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            sync_panels(
                ScriptClient([[panel(1, 0), panel(2, 1), panel(3, 2)]]),
                state,
            )
            before = [panel(1, 0), panel(2, 1, "new"), panel(3, 2, "new")]
            after = [panel(1, 0, "new"), panel(2, 1, "new"), panel(3, 2, "new")]
            result = sync_panels(ScriptClient([before, after]), state)
            self.assertEqual(result.source_screen, 1)
            self.assertFalse(result.conflict)

    def test_incompatible_edits_fall_back_to_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            sync_panels(
                ScriptClient([[panel(1, 0), panel(2, 1), panel(3, 2)]]),
                state,
            )
            before = [panel(1, 0), panel(2, 1, "two"), panel(3, 2, "three")]
            after = [panel(1, 0), panel(2, 1), panel(3, 2)]
            result = sync_panels(ScriptClient([before, after]), state)
            self.assertEqual(result.source_screen, 0)
            self.assertTrue(result.conflict)

    def test_query_rejects_invalid_payload(self) -> None:
        client = ScriptClient([[{"id": 1}]])
        with self.assertRaises(ValueError):
            query_panels(client)


if __name__ == "__main__":
    unittest.main()
