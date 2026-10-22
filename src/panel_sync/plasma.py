"""KDE Plasma scripting through qdbus."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

from panel_sync.core import effective_launchers
from panel_sync.displays import (
    DisplayLayout,
    discover_display_layout,
    find_kscreen_doctor,
)
from panel_sync.model import TaskManager
from panel_sync.state import State, previous_for_widget


SUPPORTED_PLUGINS = (
    "org.kde.plasma.icontasks",
    "org.kde.plasma.taskmanager",
)
CACHE_REFRESH_TIMEOUT = 30

QUERY_SCRIPT = r"""
var supported = {
    "org.kde.plasma.icontasks": true,
    "org.kde.plasma.taskmanager": true
};
var result = [];
for (var panelIndex = 0; panelIndex < panelIds.length; ++panelIndex) {
    var panel = panelById(panelIds[panelIndex]);
    if (!panel || panel.screen < 0 || panel.screen >= screenCount) continue;
    var widgets = panel.widgets();
    for (var widgetIndex = 0; widgetIndex < widgets.length; ++widgetIndex) {
        var widget = widgets[widgetIndex];
        if (!supported[widget.type]) continue;
        widget.currentConfigGroup = [];
        var root = widget.readConfig("launchers", "");
        widget.currentConfigGroup = ["General"];
        var general = widget.readConfig("launchers", "");
        result.push({
            panel: panel.id,
            screen: panel.screen,
            widget: widget.id,
            plugin: widget.type,
            root: String(root || ""),
            general: String(general || "")
        });
    }
}
print(JSON.stringify(result));
"""


def find_qdbus(explicit: str | None = None) -> str:
    """Find a Qt 6 qdbus command used by common distributions."""

    requested = explicit or os.environ.get("PLASMA_TASKBAR_SYNC_QDBUS")
    if requested:
        resolved = shutil.which(requested)
        if not resolved:
            raise FileNotFoundError(f"qdbus command not found: {requested}")
        return resolved

    for name in ("qdbus6", "qdbus-qt6"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise FileNotFoundError(
        "Qt 6 qdbus was not found; install your distribution's Qt 6 tools package"
    )


def find_kbuildsycoca() -> str:
    """Find KDE's application cache builder."""

    resolved = shutil.which("kbuildsycoca6")
    if resolved:
        return resolved
    raise FileNotFoundError(
        "kbuildsycoca6 was not found; install your distribution's KDE tools package"
    )


def _launcher_array(launchers: str) -> str:
    return json.dumps([item for item in launchers.split(",") if item])


class PlasmaClient:
    """Read and write task manager settings through Plasma."""

    def __init__(self, qdbus: str | None = None, timeout: float = 8) -> None:
        self.qdbus = find_qdbus(qdbus)
        self.kbuildsycoca = find_kbuildsycoca()
        self.kscreen_doctor = find_kscreen_doctor()
        self.timeout = timeout

    def evaluate(self, script: str) -> str:
        """Run a script inside Plasma and return its printed output."""

        result = subprocess.run(
            [
                self.qdbus,
                "org.kde.plasmashell",
                "/PlasmaShell",
                "org.kde.PlasmaShell.evaluateScript",
                script,
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout,
        )
        return result.stdout.strip()

    def refresh_launcher_model(self) -> None:
        """Make Plasma redraw launcher changes written through its script API."""

        subprocess.run(
            [self.kbuildsycoca, "--noincremental"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=CACHE_REFRESH_TIMEOUT,
        )

    def display_layout(self) -> DisplayLayout:
        """Return connected outputs mapped to Plasma screen numbers."""

        return discover_display_layout(
            self,
            self.kscreen_doctor,
            timeout=self.timeout,
        )

    def task_managers(self, state: State) -> list[TaskManager]:
        output = self.evaluate(QUERY_SCRIPT)
        records = json.loads(output or "[]")
        if not isinstance(records, list):
            raise ValueError("Plasma returned invalid task manager data")

        managers = [self._parse_manager(record, state) for record in records]
        return sorted(
            managers,
            key=lambda item: (item.screen, item.panel, item.widget),
        )

    @staticmethod
    def _parse_manager(record: Any, state: State) -> TaskManager:
        if not isinstance(record, dict):
            raise ValueError("Plasma returned an invalid task manager record")
        try:
            panel = int(record["panel"])
            screen = int(record["screen"])
            widget = int(record["widget"])
            plugin = str(record["plugin"])
            root = str(record["root"])
            general = str(record["general"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "Plasma returned an invalid task manager record"
            ) from error
        if plugin not in SUPPORTED_PLUGINS:
            raise ValueError(f"Plasma returned an unsupported task manager: {plugin}")
        previous = previous_for_widget(state, widget)
        launchers = effective_launchers(root, general, previous)
        return TaskManager(
            panel=panel,
            screen=screen,
            widget=widget,
            plugin=plugin,
            root=root,
            general=general,
            launchers=launchers,
        )

    def set_all_launchers(self, launchers: str) -> None:
        target = _launcher_array(launchers)
        plugins = json.dumps({plugin: True for plugin in SUPPORTED_PLUGINS})
        script = f"""
var target = {target};
var supported = {plugins};
for (var panelIndex = 0; panelIndex < panelIds.length; ++panelIndex) {{
    var panel = panelById(panelIds[panelIndex]);
    if (!panel || panel.screen < 0 || panel.screen >= screenCount) continue;
    var widgets = panel.widgets();
    for (var widgetIndex = 0; widgetIndex < widgets.length; ++widgetIndex) {{
        var widget = widgets[widgetIndex];
        if (!supported[widget.type]) continue;
        widget.currentConfigGroup = [];
        widget.writeConfig("launchers", target);
        widget.currentConfigGroup = ["General"];
        widget.writeConfig("launchers", target);
        widget.currentConfigGroup = [];
        widget.reloadConfig();
    }}
}}
"""
        self.evaluate(script)
        self.refresh_launcher_model()

    def set_widget_keys(self, widget_id: int, root: str, general: str) -> None:
        root_target = _launcher_array(root)
        general_target = _launcher_array(general)
        plugins = json.dumps({plugin: True for plugin in SUPPORTED_PLUGINS})
        script = f"""
var targetWidget = {int(widget_id)};
var rootTarget = {root_target};
var generalTarget = {general_target};
var supported = {plugins};
for (var panelIndex = 0; panelIndex < panelIds.length; ++panelIndex) {{
    var panel = panelById(panelIds[panelIndex]);
    if (!panel || panel.screen < 0 || panel.screen >= screenCount) continue;
    var widgets = panel.widgets();
    for (var widgetIndex = 0; widgetIndex < widgets.length; ++widgetIndex) {{
        var widget = widgets[widgetIndex];
        if (widget.id !== targetWidget) continue;
        if (!supported[widget.type]) continue;
        widget.currentConfigGroup = [];
        widget.writeConfig("launchers", rootTarget);
        widget.currentConfigGroup = ["General"];
        widget.writeConfig("launchers", generalTarget);
        widget.currentConfigGroup = [];
        widget.reloadConfig();
    }}
}}
"""
        self.evaluate(script)
