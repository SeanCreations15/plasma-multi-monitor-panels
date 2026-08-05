from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from plasma_taskbar_sync.displays import (
    DisplayOutput,
    Geometry,
    PlasmaScreen,
    discover_display_layout,
    map_outputs_to_screens,
    parse_kscreen_outputs,
)


def output(
    output_id: int,
    name: str,
    priority: int,
    geometry: Geometry,
) -> DisplayOutput:
    return DisplayOutput(output_id, name, priority, geometry)


class ScreenClient:
    def evaluate(self, _script: str) -> str:
        return json.dumps(
            [
                {"screen": 0, "x": 1920, "y": 0, "width": 2560, "height": 1440},
                {"screen": 1, "x": 0, "y": 0, "width": 1920, "height": 1080},
            ]
        )


class OutputParsingTests(unittest.TestCase):
    def test_enabled_outputs_use_scaled_logical_geometry(self) -> None:
        payload = json.dumps(
            {
                "outputs": [
                    {
                        "id": 7,
                        "name": "DP-1",
                        "connected": True,
                        "enabled": True,
                        "priority": 1,
                        "replicationSource": 0,
                        "currentModeId": "mode",
                        "modes": [
                            {
                                "id": "mode",
                                "size": {"width": 3840, "height": 2160},
                            }
                        ],
                        "pos": {"x": 1920, "y": 0},
                        "scale": 1.5,
                        "rotation": 1,
                    },
                    {
                        "id": 8,
                        "name": "DP-2",
                        "connected": True,
                        "enabled": False,
                    },
                ]
            }
        )
        outputs = parse_kscreen_outputs(payload)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].geometry, Geometry(1920, 0, 2560, 1440))

    def test_mirrored_output_is_rejected_safely(self) -> None:
        payload = json.dumps(
            {
                "outputs": [
                    {
                        "id": 1,
                        "name": "DP-1",
                        "connected": True,
                        "enabled": True,
                        "priority": 1,
                        "replicationSource": 0,
                        "currentModeId": "1",
                        "modes": [
                            {"id": "1", "size": {"width": 1920, "height": 1080}}
                        ],
                        "pos": {"x": 0, "y": 0},
                        "scale": 1,
                        "rotation": 1,
                    },
                    {
                        "id": 2,
                        "name": "HDMI-A-1",
                        "connected": True,
                        "enabled": True,
                        "priority": 2,
                        "replicationSource": 1,
                    }
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "mirrored"):
            parse_kscreen_outputs(payload)


class ScreenMappingTests(unittest.TestCase):
    def test_geometry_maps_connector_and_real_primary_screen(self) -> None:
        outputs = [
            output(1, "HDMI-A-1", 2, Geometry(0, 0, 1920, 1080)),
            output(2, "DP-1", 1, Geometry(1920, 0, 2560, 1440)),
        ]
        screens = [
            PlasmaScreen(0, Geometry(1920, 0, 2560, 1440)),
            PlasmaScreen(1, Geometry(0, 0, 1920, 1080)),
        ]
        layout = map_outputs_to_screens(outputs, screens)
        self.assertEqual(layout.primary_screen, 0)
        self.assertEqual(layout.name_for_screen(0), "DP-1")
        self.assertEqual(layout.name_for_screen(1), "HDMI-A-1")

    def test_transient_output_count_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "still settling"):
            map_outputs_to_screens(
                [output(1, "DP-1", 1, Geometry(0, 0, 1920, 1080))],
                [],
            )

    def test_discovery_runs_kscreen_doctor(self) -> None:
        payload = json.dumps(
            {
                "outputs": [
                    {
                        "id": 1,
                        "name": "DP-1",
                        "connected": True,
                        "enabled": True,
                        "priority": 1,
                        "replicationSource": 0,
                        "currentModeId": "1",
                        "modes": [
                            {"id": "1", "size": {"width": 1920, "height": 1080}}
                        ],
                        "pos": {"x": 0, "y": 0},
                        "scale": 1,
                        "rotation": 1,
                    },
                    {
                        "id": 2,
                        "name": "DP-2",
                        "connected": True,
                        "enabled": True,
                        "priority": 2,
                        "replicationSource": 0,
                        "currentModeId": "2",
                        "modes": [
                            {"id": "2", "size": {"width": 2560, "height": 1440}}
                        ],
                        "pos": {"x": 1920, "y": 0},
                        "scale": 1,
                        "rotation": 1,
                    },
                ]
            }
        )
        completed = subprocess.CompletedProcess(
            ["/usr/bin/kscreen-doctor", "-j"],
            0,
            stdout=payload,
            stderr="",
        )
        with patch(
            "plasma_taskbar_sync.displays.subprocess.run",
            return_value=completed,
        ) as run:
            layout = discover_display_layout(
                ScreenClient(),
                "/usr/bin/kscreen-doctor",
            )
        self.assertEqual(layout.primary_screen, 1)
        run.assert_called_once_with(
            ["/usr/bin/kscreen-doctor", "-j"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )


if __name__ == "__main__":
    unittest.main()
