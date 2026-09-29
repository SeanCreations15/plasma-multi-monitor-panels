from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from panel_sync.displays import (
    DisplayLayout,
    DisplayOutput,
    Geometry,
    MappedOutput,
)
from panel_sync.panels import (
    PANEL_QUERY_SCRIPT,
    panels_match,
    query_panels,
    sync_panels,
)


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


def panel_with_default(
    panel_id: int,
    screen: int,
    value: str = "same",
    *,
    explicit_default: bool = False,
) -> dict[str, object]:
    result = panel(panel_id, screen, value)
    if explicit_default:
        result["widgets"][0]["config"]["keys"]["defaultValue"] = "default"
    return result


def layout(primary: int, *screens: int) -> DisplayLayout:
    mapped = tuple(
        MappedOutput(
            DisplayOutput(
                output_id=screen + 10,
                name=f"DP-{screen}",
                priority=1 if screen == primary else screen + 2,
                geometry=Geometry(screen * 1920, 0, 1920, 1080),
            ),
            screen,
        )
        for screen in screens
    )
    return DisplayLayout(mapped, primary)


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
    def test_query_ignores_transient_widget_window_geometry(self) -> None:
        self.assertIn("popupHeight: true, popupWidth: true", PANEL_QUERY_SCRIPT)
        self.assertIn('groups[j] === "ConfigDialog"', PANEL_QUERY_SCRIPT)

    def test_query_normalizes_adaptive_clock_date_location(self) -> None:
        self.assertIn(
            'widget.type === "org.kde.plasma.digitalclock"',
            PANEL_QUERY_SCRIPT,
        )
        self.assertIn(
            'widget.readConfig("dateDisplayFormat", "Adaptive")',
            PANEL_QUERY_SCRIPT,
        )

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
            self.assertIn('keys[i] === "launchers"', client.scripts[1])
            self.assertIn('value.split(",")', client.scripts[1])

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

    def test_per_screen_baselines_ignore_equivalent_serialization_differences(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            initial = [
                panel_with_default(1, 0),
                panel_with_default(2, 1, explicit_default=True),
                panel_with_default(3, 2, explicit_default=True),
            ]
            sync_panels(ScriptClient([initial]), state)
            before = [
                panel_with_default(1, 0),
                panel_with_default(2, 1, "new", explicit_default=True),
                panel_with_default(3, 2, explicit_default=True),
            ]
            after = [
                panel_with_default(1, 0, "new", explicit_default=True),
                panel_with_default(2, 1, "new", explicit_default=True),
                panel_with_default(3, 2, "new", explicit_default=True),
            ]
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

    def test_real_primary_display_is_initial_source(self) -> None:
        before = [panel(1, 0, "secondary"), panel(2, 1, "primary")]
        after = [panel(1, 0, "primary"), panel(2, 1, "primary")]
        client = ScriptClient([before, after])
        with tempfile.TemporaryDirectory() as directory:
            result = sync_panels(
                client,
                Path(directory) / "state.json",
                layout(1, 1, 0),
            )
        self.assertEqual(result.source_screen, 1)
        self.assertTrue(result.changed)

    def test_missing_panels_are_created_and_receive_source_profile(self) -> None:
        source = panel(1, 1, "source")
        created_primary = panel(2, 0, "blank")
        created_third = panel(3, 2, "blank")
        synced = [
            source,
            panel(2, 0, "source"),
            panel(3, 2, "source"),
        ]
        client = ScriptClient(
            [
                [source],
                [source, created_primary, created_third],
                synced,
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            result = sync_panels(
                client,
                Path(directory) / "state.json",
                layout(0, 0, 1, 2),
            )
        self.assertEqual(result.created, 2)
        self.assertEqual(result.panels, 3)
        self.assertEqual(result.source_screen, 1)
        self.assertIn("panel.screen = targetScreens[i]", client.scripts[1])
        self.assertIn("var destinationIds = [2, 3]", client.scripts[3])

    def test_missing_panel_dry_run_does_not_create(self) -> None:
        client = ScriptClient([[panel(1, 0)]])
        with tempfile.TemporaryDirectory() as directory:
            result = sync_panels(
                client,
                Path(directory) / "state.json",
                layout(0, 0, 1),
                dry_run=True,
            )
        self.assertTrue(result.changed)
        self.assertEqual(result.created, 1)
        self.assertEqual(len(client.scripts), 1)

    def test_multiple_panels_on_one_output_are_not_overwritten(self) -> None:
        client = ScriptClient([[panel(1, 0), panel(2, 0)]])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "multiple panels"):
                sync_panels(
                    client,
                    Path(directory) / "state.json",
                    layout(0, 0, 1),
                )

    def test_no_existing_panel_requires_user_source(self) -> None:
        client = ScriptClient([[]])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "add one Plasma panel"):
                sync_panels(
                    client,
                    Path(directory) / "state.json",
                    layout(0, 0, 1),
                )


if __name__ == "__main__":
    unittest.main()
