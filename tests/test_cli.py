from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from plasma_taskbar_sync.cli import (
    ServiceDetails,
    _test_launchers,
    build_parser,
    concise_error,
    command_doctor,
    command_status,
    main,
)
from plasma_taskbar_sync.model import TaskManager
from plasma_taskbar_sync.state import State


class StatusClient:
    def __init__(self, managers: list[TaskManager]) -> None:
        self.managers = managers

    def task_managers(self, _state: State) -> list[TaskManager]:
        return self.managers


class FailingStatusClient:
    def task_managers(self, _state: State) -> list[TaskManager]:
        raise OSError("Plasma bus unavailable")


class ParserTests(unittest.TestCase):
    def test_command_is_required(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args([])

    def test_watch_rejects_tight_loop(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["watch", "--interval", "0.1"])

    def test_dry_run_parses(self) -> None:
        args = build_parser().parse_args(["once", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_self_test_always_changes_launcher_list(self) -> None:
        original = ",".join(
            [
                "applications:systemsettings.desktop",
                "applications:org.kde.discover.desktop",
                "applications:org.kde.kcalc.desktop",
                "applications:plasma-taskbar-sync-self-test.desktop",
            ]
        )
        self.assertNotEqual(",".join(_test_launchers(original)), original)

    def test_cache_timeout_has_a_specific_error(self) -> None:
        error = subprocess.TimeoutExpired(
            ["/usr/bin/kbuildsycoca6", "--noincremental"],
            timeout=30,
        )
        self.assertEqual(
            concise_error(error),
            "KDE's application cache refresh timed out",
        )


class StatusTests(unittest.TestCase):
    def test_different_plasma_keys_are_not_in_sync(self) -> None:
        manager = TaskManager(
            panel=1,
            screen=0,
            widget=2,
            plugin="org.kde.plasma.icontasks",
            root="stale",
            general="current",
            launchers="current",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch(
                "plasma_taskbar_sync.cli.service_details",
                return_value=ServiceDetails("active", "enabled"),
            ):
                with redirect_stdout(output):
                    result = command_status(
                        StatusClient([manager]),
                        Path(directory) / "state.json",
                    )

        self.assertEqual(result, 1)
        self.assertEqual(
            output.getvalue(),
            "Taskbars: out of sync (1 found)\nService: running\n",
        )

    def test_stopped_service_is_unhealthy(self) -> None:
        managers = [
            TaskManager(1, 0, 2, "org.kde.plasma.icontasks", "a", "a", "a"),
            TaskManager(3, 1, 4, "org.kde.plasma.icontasks", "a", "a", "a"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch(
                "plasma_taskbar_sync.cli.service_details",
                return_value=ServiceDetails("inactive", "enabled"),
            ):
                with redirect_stdout(output):
                    result = command_status(
                        StatusClient(managers),
                        Path(directory) / "state.json",
                    )

        self.assertEqual(result, 1)
        self.assertIn("Taskbars: in sync", output.getvalue())
        self.assertIn("Service: not running (inactive)", output.getvalue())

    def test_service_state_is_shown_when_plasma_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch(
                "plasma_taskbar_sync.cli.service_details",
                return_value=ServiceDetails("active", "enabled"),
            ):
                with redirect_stdout(output):
                    result = command_status(
                        FailingStatusClient(),
                        Path(directory) / "state.json",
                    )

        self.assertEqual(result, 1)
        self.assertEqual(
            output.getvalue(),
            "Taskbars: unknown\n"
            "  Plasma bus unavailable\n"
            "Service: running\n",
        )


class DoctorTests(unittest.TestCase):
    def test_doctor_prints_full_task_manager_details(self) -> None:
        managers = [
            TaskManager(
                panel=10,
                screen=0,
                widget=20,
                plugin="org.kde.plasma.icontasks",
                root="a",
                general="a",
                launchers="a",
            ),
            TaskManager(
                panel=11,
                screen=1,
                widget=21,
                plugin="org.kde.plasma.icontasks",
                root="a",
                general="a",
                launchers="a",
            ),
        ]
        client = StatusClient(managers)
        client.qdbus = "/usr/bin/qdbus6"
        client.kbuildsycoca = "/usr/bin/kbuildsycoca6"

        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch(
                    "plasma_taskbar_sync.cli.service_details",
                    return_value=ServiceDetails("active", "enabled"),
                ),
                patch(
                    "plasma_taskbar_sync.cli.find_qdbus",
                    return_value=client.qdbus,
                ),
                patch(
                    "plasma_taskbar_sync.cli.find_kbuildsycoca",
                    return_value=client.kbuildsycoca,
                ),
                patch(
                    "plasma_taskbar_sync.cli.PlasmaClient",
                    return_value=client,
                ),
                redirect_stdout(output),
            ):
                result = command_doctor(
                    Path(directory) / "state.json",
                )

        self.assertEqual(result, 0)
        text = output.getvalue()
        self.assertIn("Plasma Taskbar Sync: 0.1.0", text)
        self.assertIn("Service state: active", text)
        self.assertIn("Task managers: 2", text)
        self.assertIn("Screen 0: panel 10, widget 20", text)
        self.assertIn("      - a", text)

    def test_doctor_reports_all_missing_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with (
                patch(
                    "plasma_taskbar_sync.cli.service_details",
                    return_value=ServiceDetails(
                        "unavailable",
                        "unavailable",
                        "user bus unavailable",
                    ),
                ),
                patch(
                    "plasma_taskbar_sync.cli.find_qdbus",
                    side_effect=FileNotFoundError("qdbus missing"),
                ),
                patch(
                    "plasma_taskbar_sync.cli.find_kbuildsycoca",
                    side_effect=FileNotFoundError(
                        "kbuildsycoca6 missing"
                    ),
                ),
                redirect_stdout(output),
            ):
                result = command_doctor(
                    Path(directory) / "state.json",
                )

        self.assertEqual(result, 1)
        text = output.getvalue()
        self.assertIn("Service check: user bus unavailable", text)
        self.assertIn("qdbus: missing", text)
        self.assertIn("kbuildsycoca: missing", text)
        self.assertIn("Result: 2 problem(s) found", text)


if __name__ == "__main__":
    unittest.main()
