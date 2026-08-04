from __future__ import annotations

import json
import unittest

from plasma_taskbar_sync.panels import panels_match, query_panels, sync_primary_panel


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
        result = sync_primary_panel(client, dry_run=True)
        self.assertTrue(result.changed)
        self.assertEqual(len(client.scripts), 1)

    def test_apply_is_verified(self) -> None:
        before = [panel(1, 0), panel(2, 1, "different")]
        after = [panel(1, 0), panel(2, 1)]
        client = ScriptClient([before, after])
        result = sync_primary_panel(client)
        self.assertTrue(result.changed)
        self.assertEqual(len(client.scripts), 3)

    def test_query_rejects_invalid_payload(self) -> None:
        client = ScriptClient([[{"id": 1}]])
        with self.assertRaises(ValueError):
            query_panels(client)


if __name__ == "__main__":
    unittest.main()
