"""Persistent state and process locking."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any, Iterator

from panel_sync.model import TaskManager


STATE_VERSION = 1
State = dict[str, Any]


def default_state_path() -> Path:
    """Return the state path for the current user."""

    return (
        Path.home()
        / ".local"
        / "state"
        / "panel-sync"
        / "state.json"
    )


def empty_state() -> State:
    """Return a new, empty state document."""

    return {"version": STATE_VERSION, "selected": None, "widgets": {}}


def _validate_state(data: Any, path: Path) -> State:
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        raise ValueError(f"Unsupported sync state in {path}")

    selected = data.get("selected")
    if selected is not None and not isinstance(selected, str):
        raise ValueError(f"Invalid selected launcher list in {path}")

    widgets = data.get("widgets")
    if not isinstance(widgets, dict):
        raise ValueError(f"Invalid widget state in {path}")
    for widget_id, widget in widgets.items():
        valid = (
            isinstance(widget_id, str)
            and isinstance(widget, dict)
            and isinstance(widget.get("panel"), int)
            and isinstance(widget.get("screen"), int)
            and isinstance(widget.get("plugin"), str)
            and isinstance(widget.get("effective"), str)
        )
        if not valid:
            raise ValueError(f"Invalid state for widget {widget_id!r} in {path}")
    return data


def read_state(path: Path) -> State:
    """Read and check state from disk."""

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_state()

    if not content.strip():
        return empty_state()

    return _validate_state(json.loads(content), path)


def write_state(path: Path, state: State) -> None:
    """Replace state on disk without leaving a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def previous_for_widget(state: State, widget_id: int) -> str | None:
    widgets = state.get("widgets", {})
    if isinstance(widgets, dict):
        saved = widgets.get(str(widget_id))
        if isinstance(saved, dict) and isinstance(saved.get("effective"), str):
            return saved["effective"]
    selected = state.get("selected")
    return selected if isinstance(selected, str) else None


def from_managers(managers: list[TaskManager], selected: str) -> State:
    widgets: dict[str, object] = {}
    for manager in managers:
        widgets[str(manager.widget)] = {
            "panel": manager.panel,
            "screen": manager.screen,
            "plugin": manager.plugin,
            "effective": manager.launchers,
        }
    return {
        "version": STATE_VERSION,
        "selected": selected,
        "widgets": widgets,
    }


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock until the context closes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
