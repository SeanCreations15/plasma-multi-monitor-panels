"""Command-line interface for Panel Sync."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from panel_sync import __version__
from panel_sync.core import sync_once
from panel_sync.events import (
    EventSource,
    InotifyEventSource,
    plasma_config_path,
)
from panel_sync.displays import find_kscreen_doctor
from panel_sync.model import TaskManager
from panel_sync.panels import backup_plasma_config, sync_panels
from panel_sync.plasma import (
    PlasmaClient,
    find_kbuildsycoca,
    find_qdbus,
)
from panel_sync.state import (
    default_state_path,
    exclusive_lock,
    read_state,
    write_state,
)


_CONFLICT_MESSAGE = (
    "Conflicting edits found; the KDE primary display won when present, "
    "otherwise the first task manager won"
)
_EXPECTED_ERRORS = (
    json.JSONDecodeError,
    OSError,
    RuntimeError,
    subprocess.SubprocessError,
    ValueError,
)
_SERVICE_NAME = "panel-sync.service"
_SYSTEMCTL_TIMEOUT = 5
_DEBOUNCE_SECONDS = 0.5
_ERROR_RETRY_SECONDS = 2.0
_POLL_FALLBACK_SECONDS = 2.0
_EVENT_RETRY_SECONDS = 60.0
_WATCH_ERROR_REPEAT_SECONDS = 300.0


@dataclass(frozen=True)
class ServiceDetails:
    active: str
    enabled: str
    error: str | None = None


def concise_error(error: BaseException) -> str:
    if isinstance(error, subprocess.CalledProcessError) and error.stderr:
        detail = error.stderr.strip()
        if detail:
            return detail
    if isinstance(error, subprocess.TimeoutExpired):
        command = error.cmd if isinstance(error.cmd, (list, tuple)) else [error.cmd]
        if command and Path(str(command[0])).name == "kbuildsycoca6":
            return "KDE's application cache refresh timed out"
        return "Plasma did not answer before the timeout"
    return str(error)


def _systemctl_value(systemctl: str, action: str) -> tuple[str, str | None]:
    try:
        result = subprocess.run(
            [systemctl, "--user", action, _SERVICE_NAME],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_SYSTEMCTL_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return "unavailable", concise_error(error)

    value = result.stdout.strip()
    if value:
        return value, None
    detail = result.stderr.strip() or f"systemctl {action} returned no status"
    return "unavailable", detail


def service_details() -> ServiceDetails:
    """Read the user service state without changing it."""

    systemctl = shutil.which("systemctl")
    if not systemctl:
        return ServiceDetails(
            active="unavailable",
            enabled="unavailable",
            error="systemctl was not found",
        )

    active, active_error = _systemctl_value(systemctl, "is-active")
    enabled, enabled_error = _systemctl_value(systemctl, "is-enabled")
    return ServiceDetails(
        active=active,
        enabled=enabled,
        error=active_error or enabled_error,
    )


def _lock_path(state_path: Path) -> Path:
    return state_path.with_name("sync.lock")


def _managers_in_sync(managers: list[TaskManager]) -> bool:
    values = {manager.launchers for manager in managers}
    return (
        bool(managers)
        and len(values) == 1
        and all(manager.root == manager.general for manager in managers)
    )


def command_status(
    client: PlasmaClient,
    state_path: Path,
    *,
    as_json: bool = False,
) -> int:
    service = service_details()
    running = service.active == "active"
    taskbar_error: str | None = None
    try:
        state = read_state(state_path)
        managers = client.task_managers(state)
        synced: bool | None = _managers_in_sync(managers)
    except _EXPECTED_ERRORS as error:
        managers = []
        synced = None
        taskbar_error = concise_error(error)

    if as_json:
        print(
            json.dumps(
                {
                    "in_sync": synced,
                    "service_running": running,
                    "service_state": service.active,
                    "taskbar_error": taskbar_error,
                    "task_managers": len(managers),
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if taskbar_error:
            print("Taskbars: unknown")
            print(f"  {taskbar_error}")
        elif not managers:
            print("Taskbars: not found")
        else:
            state_label = "in sync" if synced else "out of sync"
            print(f"Taskbars: {state_label} ({len(managers)} found)")

        if running:
            print("Service: running")
        elif service.active == "unavailable":
            print("Service: unknown")
            if service.error:
                print(f"  {service.error}")
        else:
            print(f"Service: not running ({service.active})")
    return 0 if synced is True and running else 1


def command_once(
    client: PlasmaClient,
    state_path: Path,
    *,
    dry_run: bool = False,
) -> int:
    with exclusive_lock(_lock_path(state_path)):
        layout = None
        if isinstance(client, PlasmaClient):
            layout = client.display_layout()
            panel_preview = sync_panels(
                client,
                state_path,
                layout,
                dry_run=True,
            )
        else:
            panel_preview = None
        if panel_preview is not None and not dry_run:
            if panel_preview.changed:
                backup_plasma_config(state_path)
            panel_result = sync_panels(client, state_path, layout)
            if panel_result.changed:
                client.refresh_launcher_model()
        else:
            panel_result = panel_preview
        result = sync_once(
            client,
            state_path,
            dry_run=dry_run,
            primary_screen=layout.primary_screen if layout is not None else 0,
        )
    if result.managers == 0:
        print("No Plasma task managers found", file=sys.stderr)
        return 1
    if result.conflict:
        print(_CONFLICT_MESSAGE, file=sys.stderr)
    if dry_run:
        action = "Would sync" if result.changed else "Already in sync"
    else:
        action = "Synced" if result.changed else "Already in sync"
    panel_action = "would sync" if dry_run else "synced"
    if panel_result is not None and panel_result.changed:
        created = (
            f", created {panel_result.created} missing"
            if panel_result.created
            else ""
        )
        print(
            f"Panels {panel_action} from screen {panel_result.source_screen}: "
            f"{panel_result.panels} panels{created}"
        )
    elif panel_result is not None:
        print(f"Panels already in sync: {panel_result.panels} panels")
    print(f"{action}: {result.managers} task managers")
    return 0


def command_watch(
    client: PlasmaClient,
    state_path: Path,
    fallback_interval: float,
    *,
    event_source_factory: Callable[[Path], EventSource] = (
        InotifyEventSource
    ),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    last_error: str | None = None
    last_error_time = 0.0
    last_watch_error: str | None = None
    last_watch_error_time = 0.0
    event_source: EventSource | None = None
    next_event_retry = monotonic()
    next_sync = next_event_retry
    change_deadline: float | None = None

    try:
        while True:
            now = monotonic()
            if event_source is None and now >= next_event_retry:
                try:
                    event_source = event_source_factory(plasma_config_path())
                except OSError as error:
                    detail = concise_error(error)
                    if (
                        detail != last_watch_error
                        or now - last_watch_error_time
                        >= _WATCH_ERROR_REPEAT_SECONDS
                    ):
                        print(
                            f"Event watch unavailable: {detail}; "
                            f"polling every {_POLL_FALLBACK_SECONDS:g} seconds",
                            file=sys.stderr,
                            flush=True,
                        )
                        last_watch_error = detail
                        last_watch_error_time = now
                    next_event_retry = now + _EVENT_RETRY_SECONDS
                else:
                    if last_watch_error is not None:
                        print("Event watch restored", flush=True)
                    last_watch_error = None

            now = monotonic()
            event_due = (
                change_deadline is not None
                and now >= change_deadline
            )
            if now >= next_sync or event_due:
                try:
                    with exclusive_lock(_lock_path(state_path)):
                        if isinstance(client, PlasmaClient):
                            layout = client.display_layout()
                            panel_preview = sync_panels(
                                client,
                                state_path,
                                layout,
                                dry_run=True,
                            )
                        else:
                            layout = None
                            panel_preview = None
                        if panel_preview is not None:
                            if panel_preview.changed:
                                backup_plasma_config(state_path)
                            panel_result = sync_panels(
                                client,
                                state_path,
                                layout,
                            )
                            if panel_result.changed:
                                client.refresh_launcher_model()
                        else:
                            panel_result = panel_preview
                        if layout is None:
                            result = sync_once(client, state_path)
                        else:
                            result = sync_once(
                                client,
                                state_path,
                                primary_screen=layout.primary_screen,
                            )
                    if result.conflict:
                        print(
                            _CONFLICT_MESSAGE,
                            file=sys.stderr,
                            flush=True,
                        )
                    if result.changed:
                        print(
                            f"Synced {result.managers} task managers",
                            flush=True,
                        )
                    if panel_result is not None and panel_result.changed:
                        detail = " (widget structure rebuilt)" if panel_result.rebuilt else ""
                        created = (
                            f", created {panel_result.created} missing"
                            if panel_result.created
                            else ""
                        )
                        print(
                            f"Synced {panel_result.panels} panels from screen "
                            f"{panel_result.source_screen}{created}{detail}",
                            flush=True,
                        )
                    if panel_result is not None and panel_result.conflict:
                        print(
                            "Conflicting panel edits found; the KDE primary "
                            "display won",
                            file=sys.stderr,
                            flush=True,
                        )
                    last_error = None
                    next_sync = now + (
                        fallback_interval
                        if event_source is not None
                        else _POLL_FALLBACK_SECONDS
                    )
                except _EXPECTED_ERRORS as error:
                    now = monotonic()
                    detail = concise_error(error)
                    if (
                        detail != last_error
                        or now - last_error_time >= 30
                    ):
                        print(
                            f"Sync check failed: {detail}",
                            file=sys.stderr,
                            flush=True,
                        )
                        last_error = detail
                        last_error_time = now
                    next_sync = now + _ERROR_RETRY_SECONDS
                change_deadline = None
                continue

            deadlines = [next_sync]
            if change_deadline is not None:
                deadlines.append(change_deadline)
            if event_source is None:
                deadlines.append(next_event_retry)
            wait_time = max(0.0, min(deadlines) - now)

            if event_source is None:
                sleep(wait_time)
                continue

            try:
                changed = event_source.wait(wait_time)
            except OSError as error:
                detail = concise_error(error)
                event_source.close()
                event_source = None
                now = monotonic()
                if (
                    detail != last_watch_error
                    or now - last_watch_error_time
                    >= _WATCH_ERROR_REPEAT_SECONDS
                ):
                    print(
                        f"Event watch failed: {detail}; "
                        f"polling every {_POLL_FALLBACK_SECONDS:g} seconds",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_watch_error = detail
                    last_watch_error_time = now
                next_event_retry = now + _EVENT_RETRY_SECONDS
                next_sync = now
                change_deadline = None
                continue

            if changed:
                change_deadline = monotonic() + _DEBOUNCE_SECONDS
    finally:
        if event_source is not None:
            event_source.close()


def command_doctor(state_path: Path, qdbus: str | None = None) -> int:
    service = service_details()
    service_path = (
        Path.home()
        / ".local"
        / "share"
        / "systemd"
        / "user"
        / _SERVICE_NAME
    )

    print(f"Panel Sync: {__version__}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Executable: {Path(sys.argv[0]).resolve()}")
    print(
        f"Service file: {service_path} "
        f"({'found' if service_path.is_file() else 'missing'})"
    )
    print(f"Service state: {service.active}")
    print(f"Service startup: {service.enabled}")
    if service.error:
        print(f"Service check: {service.error}")
    print(
        f"State file: {state_path} "
        f"({'found' if state_path.is_file() else 'not created'})"
    )

    problems: list[str] = []
    try:
        state = read_state(state_path)
        print("State file check: OK")
    except (json.JSONDecodeError, OSError, ValueError) as error:
        state = None
        detail = concise_error(error)
        problems.append(f"state file: {detail}")
        print(f"State file check: failed ({detail})")

    try:
        qdbus_path = find_qdbus(qdbus)
        print(f"qdbus: {qdbus_path}")
    except FileNotFoundError as error:
        qdbus_path = None
        problems.append(str(error))
        print(f"qdbus: missing ({error})")

    try:
        kbuildsycoca_path = find_kbuildsycoca()
        print(f"kbuildsycoca: {kbuildsycoca_path}")
    except FileNotFoundError as error:
        kbuildsycoca_path = None
        problems.append(str(error))
        print(f"kbuildsycoca: missing ({error})")

    try:
        kscreen_doctor_path = find_kscreen_doctor()
        print(f"kscreen-doctor: {kscreen_doctor_path}")
    except FileNotFoundError as error:
        kscreen_doctor_path = None
        problems.append(str(error))
        print(f"kscreen-doctor: missing ({error})")

    if (
        state is None
        or qdbus_path is None
        or kbuildsycoca_path is None
        or kscreen_doctor_path is None
    ):
        print("Plasma connection: not checked")
        print(f"Result: {len(problems)} problem(s) found")
        return 1

    client = PlasmaClient(qdbus_path)
    try:
        layout = client.display_layout()
        managers = client.task_managers(state)
    except _EXPECTED_ERRORS as error:
        print(f"Plasma connection: failed ({concise_error(error)})")
        print("Result: Plasma is not available")
        return 1

    print("Plasma connection: OK")
    primary_name = layout.name_for_screen(layout.primary_screen) or "unknown"
    print(f"Connected outputs: {len(layout.outputs)}")
    print(f"Primary output: {primary_name} (screen {layout.primary_screen})")
    for output in layout.outputs:
        print(
            f"  {output.output.name}: screen {output.screen}, "
            f"priority {output.output.priority}"
        )
    print(f"Task managers: {len(managers)}")
    print(f"Taskbars in sync: {'yes' if _managers_in_sync(managers) else 'no'}")
    for manager in managers:
        print(
            f"  Screen {manager.screen}: panel {manager.panel}, "
            f"widget {manager.widget}"
        )
        print(f"    Plugin: {manager.plugin}")
        key_state = "match" if manager.root == manager.general else "differ"
        print(f"    Plasma launcher keys: {key_state}")
        launchers = [
            item for item in manager.launchers.split(",") if item
        ]
        print(f"    Launchers: {len(launchers)}")
        for launcher in launchers:
            print(f"      - {launcher}")
        if manager.root != manager.general:
            print(f"    Root value: {manager.root or '(empty)'}")
            print(f"    General value: {manager.general or '(empty)'}")

    if len(managers) < 2:
        print("Result: sync needs task managers on at least two displays")
        return 1
    print("Result: ready to sync")
    return 0


def _test_launchers(original: str) -> list[str]:
    items = [item for item in original.split(",") if item]
    markers = [
        "applications:systemsettings.desktop",
        "applications:org.kde.discover.desktop",
        "applications:org.kde.kcalc.desktop",
        "applications:panel-sync-self-test.desktop",
    ]
    for marker in markers:
        if marker not in items:
            items.append(marker)
        if len(items) >= 2 and ",".join(items) != original:
            break
    if ",".join(items) == original:
        items = items[:-1]
    return items


def _run_self_test_case(
    client: PlasmaClient,
    state_path: Path,
    *,
    manager_count: int,
    target: TaskManager,
    label: str,
    launchers: str,
) -> bool:
    print(f"{label}: changing only screen {target.screen}")
    client.set_widget_keys(target.widget, launchers, launchers)
    time.sleep(0.3)
    sync_once(client, state_path)
    managers = client.task_managers(read_state(state_path))
    return len(managers) == manager_count and all(
        manager.launchers == launchers for manager in managers
    )


def command_self_test(client: PlasmaClient, state_path: Path) -> int:
    with exclusive_lock(_lock_path(state_path)):
        original_state_exists = state_path.exists()
        original_state = read_state(state_path)
        original_managers = client.task_managers(original_state)
        if len(original_managers) < 2:
            print(
                "Self-test needs task managers on at least two displays",
                file=sys.stderr,
            )
            return 1

        results = {"pin": False, "order": False, "empty": False}
        restore_failed = False
        try:
            sync_once(client, state_path)
            managers = client.task_managers(read_state(state_path))
            base = managers[0].launchers
            test_items = _test_launchers(base)
            test_value = ",".join(test_items)
            reordered = ",".join(test_items[1:] + test_items[:1])
            target = managers[-1]

            for name, label, launchers in (
                ("pin", "Pin test", test_value),
                ("order", "Order test", reordered),
                ("empty", "Empty-list test", ""),
            ):
                results[name] = _run_self_test_case(
                    client,
                    state_path,
                    manager_count=len(managers),
                    target=target,
                    label=label,
                    launchers=launchers,
                )
        finally:
            for manager in original_managers:
                try:
                    client.set_widget_keys(
                        manager.widget,
                        manager.root,
                        manager.general,
                    )
                except _EXPECTED_ERRORS as error:
                    restore_failed = True
                    print(
                        f"Could not restore widget {manager.widget}: "
                        f"{concise_error(error)}",
                        file=sys.stderr,
                    )
            try:
                if original_state_exists:
                    write_state(state_path, original_state)
                else:
                    state_path.unlink(missing_ok=True)
            except OSError as error:
                restore_failed = True
                print(
                    f"Could not restore the prior state file: {error}",
                    file=sys.stderr,
                )

    restored_message = (
        "Could not fully restore the original pinned apps"
        if restore_failed
        else "Restored the original pinned apps"
    )
    if all(results.values()) and not restore_failed:
        print("PASS: pin, order, and empty-list changes reached every task manager")
        print(restored_message)
        return 0
    print(
        "FAIL: "
        f"pin={'pass' if results['pin'] else 'fail'}, "
        f"order={'pass' if results['order'] else 'fail'}, "
        f"empty={'pass' if results['empty'] else 'fail'}",
        file=sys.stderr,
    )
    print(restored_message, file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="panel-sync",
        description=(
            "Create and synchronize KDE Plasma panels across connected displays."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=default_state_path(),
        help="override the state file path",
    )
    parser.add_argument(
        "--qdbus",
        help="override the Qt 6 qdbus command",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", help="show sync and service health")
    status.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable output",
    )

    once = commands.add_parser("once", help="run one sync pass")
    once.add_argument(
        "--dry-run",
        action="store_true",
        help="show whether a write is needed without changing Plasma or state",
    )

    watch = commands.add_parser("watch", help="keep syncing until stopped")
    watch.add_argument(
        "--fallback-interval",
        type=float,
        default=60.0,
        help="maximum seconds between full checks (default: 60)",
    )

    commands.add_parser("doctor", help="show full diagnostic information")
    commands.add_parser(
        "self-test",
        help="change and restore launchers to test live syncing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (
        args.command == "watch"
        and args.fallback_interval < 0.25
    ):
        parser.error("--fallback-interval must be at least 0.25 seconds")

    try:
        if args.command == "doctor":
            return command_doctor(args.state_file, args.qdbus)

        client = PlasmaClient(args.qdbus)
        if args.command == "status":
            return command_status(client, args.state_file, as_json=args.json)
        if args.command == "once":
            return command_once(client, args.state_file, dry_run=args.dry_run)
        if args.command == "watch":
            return command_watch(
                client,
                args.state_file,
                args.fallback_interval,
            )
        if args.command == "self-test":
            return command_self_test(client, args.state_file)
    except KeyboardInterrupt:
        return 130
    except _EXPECTED_ERRORS as error:
        print(f"panel-sync: {concise_error(error)}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


def entrypoint() -> None:
    raise SystemExit(main())
