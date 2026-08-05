"""Launcher selection and sync rules."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Protocol

from plasma_taskbar_sync.model import SyncDecision, SyncResult, TaskManager
from plasma_taskbar_sync.state import (
    State,
    from_managers,
    previous_for_widget,
    read_state,
    write_state,
)


class PlasmaSyncClient(Protocol):
    def task_managers(self, state: State) -> list[TaskManager]: ...

    def set_all_launchers(self, launchers: str) -> None: ...


def _primary_manager(
    managers: list[TaskManager], primary_screen: int
) -> TaskManager:
    """Return the primary-screen manager, or the first available manager."""

    return next(
        (manager for manager in managers if manager.screen == primary_screen),
        managers[0],
    )


def effective_launchers(root: str, general: str, previous: str | None) -> str:
    """Resolve Plasma's two launcher keys without losing an empty edit."""

    if root == general:
        return root
    if previous is not None:
        root_changed = root != previous
        general_changed = general != previous
        if root_changed and not general_changed:
            return root
        if general_changed and not root_changed:
            return general
        if root_changed:
            return root
    return general if general else root


def choose_launchers(
    managers: list[TaskManager],
    state: State,
    *,
    primary_screen: int = 0,
) -> SyncDecision:
    """Choose the list changed since the last pass."""

    if not managers:
        return SyncDecision(None)

    if state.get("selected") is None and not state.get("widgets"):
        return SyncDecision(_primary_manager(managers, primary_screen).launchers)

    saved_widgets = state.get("widgets", {})
    known_widget_ids = (
        set(saved_widgets) if isinstance(saved_widgets, dict) else set()
    )
    changed = [
        manager
        for manager in managers
        if str(manager.widget) in known_widget_ids
        and manager.launchers != previous_for_widget(state, manager.widget)
    ]
    if not changed:
        selected = state.get("selected")
        value = (
            selected
            if isinstance(selected, str)
            else _primary_manager(managers, primary_screen).launchers
        )
        return SyncDecision(value)

    changed_values = {manager.launchers for manager in changed}
    if len(changed_values) == 1:
        return SyncDecision(changed[0].launchers)

    return SyncDecision(
        _primary_manager(managers, primary_screen).launchers,
        conflict=True,
    )


def sync_once(
    client: PlasmaSyncClient,
    state_path: Path,
    *,
    dry_run: bool = False,
    primary_screen: int = 0,
) -> SyncResult:
    """Run one sync pass against a Plasma client."""

    state = read_state(state_path)
    managers = client.task_managers(state)
    decision = choose_launchers(
        managers,
        state,
        primary_screen=primary_screen,
    )
    if decision.launchers is None:
        return SyncResult(
            managers=0,
            changed=False,
            conflict=False,
            launchers=None,
        )

    needs_write = any(
        item.root != decision.launchers or item.general != decision.launchers
        for item in managers
    )
    synced = [
        replace(
            item,
            root=decision.launchers,
            general=decision.launchers,
            launchers=decision.launchers,
        )
        for item in managers
    ]
    if needs_write and not dry_run:
        client.set_all_launchers(decision.launchers)
    if not dry_run:
        write_state(state_path, from_managers(synced, decision.launchers))

    return SyncResult(
        managers=len(managers),
        changed=needs_write,
        conflict=decision.conflict,
        launchers=decision.launchers,
    )
