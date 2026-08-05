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
    command_watch,
    main,
)
from plasma_taskbar_sync.events import EventSource
from plasma_taskbar_sync.displays import (
    DisplayLayout,
    DisplayOutput,
    Geometry,
    MappedOutput,
)
from plasma_taskbar_sync.model import SyncResult, TaskManager
from plasma_taskbar_sync.state import State


class StatusClient:
    def __init__(self, managers: list[TaskManager]) -> None:
        self.managers = managers

    def task_managers(self, _state: State) -> list[TaskManager]:
        return self.managers

    def display_layout(self) -> DisplayLayout:
        return DisplayLayout(
            (
                MappedOutput(
                    DisplayOutput(1, "DP-1", 1, Geometry(0, 0, 1920, 1080)),
                    0,
                ),
                MappedOutput(
                    DisplayOutput(2, "DP-2", 2, Geometry(1920, 0, 1920, 1080)),
                    1,
                ),
            ),
            0,
        )


class FailingStatusClient:
    def task_managers(self, _state: State) -> list[TaskManager]:
        raise OSError("Plasma bus unavailable")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class TimedEventSource:
    def __init__(
        self,
        clock: FakeClock,
        *,
        events: list[float] | None = None,
        stop_at: float,
    ) -> None:
        self.clock = clock
        self.events = list(events or [])
        self.stop_at = stop_at
        self.closed = False

    def wait(self, timeout: float) -> bool:
        deadline = self.clock.now + timeout
        if self.events and self.events[0] <= deadline:
            self.clock.now = self.events.pop(0)
            return True
        if self.stop_at <= deadline:
            self.clock.now = self.stop_at
            raise KeyboardInterrupt
        self.clock.now = deadline
        return False

    def close(self) -> None:
        self.closed = True


class FailingEventSource:
    def __init__(self) -> None:
        self.closed = False

    def wait(self, _timeout: float) -> bool:
        raise OSError("watch was lost")

    def close(self) -> None:
        self.closed = True


def unchanged_result() -> SyncResult:
    return SyncResult(
        managers=2,
        changed=False,
        conflict=False,
        launchers="a",
    )


class ParserTests(unittest.TestCase):
    def test_command_is_required(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args([])

    def test_watch_rejects_tight_loop(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main(["watch", "--fallback-interval", "0.1"])

    def test_watch_rejects_old_interval_option(self) -> None:
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build_parser().parse_args(["watch", "--interval", "2"])

    def test_watch_fallback_interval_parses(self) -> None:
        args = build_parser().parse_args(
            ["watch", "--fallback-interval", "90"]
        )
        self.assertEqual(args.fallback_interval, 90)

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


class WatchTests(unittest.TestCase):
    def test_watch_is_ready_before_the_initial_sync(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(clock, stop_at=0)
        order: list[str] = []

        def source_factory(_path: Path) -> TimedEventSource:
            order.append("watch")
            return source

        def run_sync(_client: object, _path: Path) -> SyncResult:
            order.append("sync")
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "plasma_taskbar_sync.cli.sync_once",
                side_effect=run_sync,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=source_factory,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(order, ["watch", "sync"])
        self.assertTrue(source.closed)

    def test_event_burst_is_debounced(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(
            clock,
            events=[0.1, 0.15],
            stop_at=1.0,
        )
        sync_times: list[float] = []

        def run_sync(_client: object, _path: Path) -> SyncResult:
            sync_times.append(clock.now)
            return unchanged_result()

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "plasma_taskbar_sync.cli.sync_once",
                side_effect=run_sync,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=lambda _path: source,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(sync_times, [0.0, 0.65])
        self.assertTrue(source.closed)

    def test_idle_watch_runs_safety_sync(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(clock, stop_at=60.1)
        sync_times: list[float] = []

        def run_sync(_client: object, _path: Path) -> SyncResult:
            sync_times.append(clock.now)
            return unchanged_result()

        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "plasma_taskbar_sync.cli.sync_once",
                side_effect=run_sync,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=lambda _path: source,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(sync_times, [0.0, 60.0])

    def test_sync_error_retries_after_two_seconds(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(clock, stop_at=2.1)
        sync_times: list[float] = []

        def run_sync(_client: object, _path: Path) -> SyncResult:
            sync_times.append(clock.now)
            if len(sync_times) == 1:
                raise OSError("Plasma is starting")
            return unchanged_result()

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "plasma_taskbar_sync.cli.sync_once",
                    side_effect=run_sync,
                ),
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=lambda _path: source,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(sync_times, [0.0, 2.0])

    def test_slow_sync_error_waits_two_seconds_after_failure(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(clock, stop_at=10.1)
        sync_times: list[float] = []

        def run_sync(_client: object, _path: Path) -> SyncResult:
            sync_times.append(clock.now)
            if len(sync_times) == 1:
                clock.now += 8.0
                raise OSError("Plasma timed out")
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "plasma_taskbar_sync.cli.sync_once",
                    side_effect=run_sync,
                ),
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=lambda _path: source,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(sync_times, [0.0, 10.0])
        self.assertTrue(source.closed)

    def test_polling_fallback_recovers_event_watch(self) -> None:
        clock = FakeClock()
        source = TimedEventSource(clock, stop_at=60.1)
        factory_times: list[float] = []
        sync_times: list[float] = []

        def source_factory(_path: Path) -> TimedEventSource:
            factory_times.append(clock.now)
            if len(factory_times) == 1:
                raise OSError("inotify blocked")
            return source

        def run_sync(_client: object, _path: Path) -> SyncResult:
            sync_times.append(clock.now)
            return unchanged_result()

        output = io.StringIO()
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "plasma_taskbar_sync.cli.sync_once",
                    side_effect=run_sync,
                ),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=source_factory,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(factory_times, [0.0, 60.0])
        self.assertEqual(sync_times[0], 0.0)
        self.assertEqual(sync_times[-1], 60.0)
        self.assertTrue(all(
            later - earlier == 2.0
            for earlier, later in zip(sync_times, sync_times[1:])
        ))
        self.assertIn("polling every 2 seconds", errors.getvalue())
        self.assertIn("Event watch restored", output.getvalue())
        self.assertTrue(source.closed)

    def test_lost_watch_switches_to_polling(self) -> None:
        clock = FakeClock()
        failed_source = FailingEventSource()
        restored_source = TimedEventSource(clock, stop_at=60.1)
        factory_times: list[float] = []

        def source_factory(_path: Path) -> EventSource:
            factory_times.append(clock.now)
            if len(factory_times) == 1:
                return failed_source
            return restored_source

        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch(
                    "plasma_taskbar_sync.cli.sync_once",
                    return_value=unchanged_result(),
                ),
                redirect_stdout(io.StringIO()),
                redirect_stderr(errors),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    command_watch(
                        object(),
                        Path(directory) / "state.json",
                        60,
                        event_source_factory=source_factory,
                        monotonic=clock.monotonic,
                        sleep=clock.sleep,
                    )

        self.assertEqual(factory_times, [0.0, 60.0])
        self.assertIn("Event watch failed", errors.getvalue())
        self.assertIn("polling every 2 seconds", errors.getvalue())
        self.assertTrue(failed_source.closed)
        self.assertTrue(restored_source.closed)


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
        self.assertIn("Plasma Taskbar Sync: 0.5.0", text)
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
