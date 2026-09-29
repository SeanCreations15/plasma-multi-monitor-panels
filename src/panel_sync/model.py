"""Data types used by the sync engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskManager:
    """A Plasma task manager and its launcher settings."""

    panel: int
    screen: int
    widget: int
    plugin: str
    root: str
    general: str
    launchers: str


@dataclass(frozen=True)
class SyncDecision:
    """The launcher list chosen for one sync pass."""

    launchers: str | None
    conflict: bool = False


@dataclass(frozen=True)
class SyncResult:
    """The result of one sync pass."""

    managers: int
    changed: bool
    conflict: bool
    launchers: str | None
