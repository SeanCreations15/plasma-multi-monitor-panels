"""Primary-authoritative Plasma panel synchronization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time
from typing import Any, Protocol


PANEL_QUERY_SCRIPT = r"""
function readTree(applet, path) {
    applet.currentConfigGroup = path;
    var result = {keys: {}, groups: {}};
    var ignoredKeys = {
        activityId: true, formfactor: true, immutability: true,
        lastScreen: true, location: true, plugin: true,
        SystrayContainmentId: true, wallpaperplugin: true
    };
    var keys = applet.configKeys.concat([]);
    for (var i = 0; i < keys.length; ++i) {
        if (ignoredKeys[keys[i]]) continue;
        var value = applet.readConfig(keys[i], "");
        if (value === undefined || value === null) value = "";
        result.keys[keys[i]] = value;
    }
    var groups = applet.configGroups.concat([]);
    for (var j = 0; j < groups.length; ++j) {
        if (groups[j] === "Applets" || groups[j] === "Shortcuts") continue;
        result.groups[groups[j]] = readTree(applet, path.concat([groups[j]]));
    }
    applet.currentConfigGroup = [];
    return result;
}

function trayTree(widget) {
    widget.currentConfigGroup = [];
    var trayId = Number(widget.readConfig("SystrayContainmentId", 0));
    if (!trayId) return null;
    var tray = desktopById(trayId);
    return tray ? readTree(tray, []) : null;
}

var result = [];
for (var i = 0; i < panelIds.length; ++i) {
    var panel = panelById(panelIds[i]);
    if (!panel) continue;
    var widgets = panel.widgets().concat([]);
    var byId = {};
    for (var wi = 0; wi < widgets.length; ++wi) byId[widgets[wi].id] = widgets[wi];
    panel.currentConfigGroup = ["General"];
    var order = String(panel.readConfig("AppletOrder", "")).split(";");
    panel.currentConfigGroup = [];
    var ordered = [];
    for (var oi = 0; oi < order.length; ++oi) {
        if (byId[order[oi]]) {
            ordered.push(byId[order[oi]]);
            delete byId[order[oi]];
        }
    }
    for (var remaining in byId) ordered.push(byId[remaining]);
    widgets = ordered;
    var widgetResult = [];
    for (var j = 0; j < widgets.length; ++j) {
        var widget = widgets[j];
        widgetResult.push({
            type: widget.type,
            index: j,
            config: readTree(widget, []),
            trayConfig: widget.type === "org.kde.plasma.systemtray"
                ? trayTree(widget) : null
        });
    }
    result.push({
        id: panel.id,
        screen: panel.screen,
        properties: {
            location: panel.location,
            height: panel.height,
            lengthMode: panel.lengthMode,
            alignment: panel.alignment,
            offset: panel.offset,
            hiding: panel.hiding
        },
        widgets: widgetResult
    });
}
print(JSON.stringify(result));
"""


@dataclass(frozen=True)
class PanelSyncResult:
    panels: int
    changed: bool
    rebuilt: bool = False


class PanelScriptClient(Protocol):
    def evaluate(self, script: str) -> str: ...


def query_panels(client: PanelScriptClient) -> list[dict[str, Any]]:
    """Return validated panel snapshots from Plasma."""

    value = json.loads(client.evaluate(PANEL_QUERY_SCRIPT) or "[]")
    if not isinstance(value, list):
        raise ValueError("Plasma returned invalid panel data")
    panels: list[dict[str, Any]] = []
    for panel in value:
        if not isinstance(panel, dict):
            raise ValueError("Plasma returned an invalid panel record")
        if not isinstance(panel.get("id"), int):
            raise ValueError("Plasma returned a panel without an id")
        if not isinstance(panel.get("screen"), int):
            raise ValueError("Plasma returned a panel without a screen")
        if not isinstance(panel.get("properties"), dict):
            raise ValueError("Plasma returned invalid panel properties")
        if not isinstance(panel.get("widgets"), list):
            raise ValueError("Plasma returned invalid panel widgets")
        panels.append(panel)
    return panels


def _tree_contains(source: Any, destination: Any) -> bool:
    """Whether destination contains all source configuration values."""

    if not isinstance(source, dict) or not isinstance(destination, dict):
        return source == destination
    source_keys = source.get("keys", {})
    destination_keys = destination.get("keys", {})
    if not isinstance(source_keys, dict) or not isinstance(destination_keys, dict):
        return False
    for key, value in source_keys.items():
        if key == "SystrayContainmentId":
            continue
        if destination_keys.get(key) != value:
            return False
    source_groups = source.get("groups", {})
    destination_groups = destination.get("groups", {})
    if not isinstance(source_groups, dict) or not isinstance(destination_groups, dict):
        return False
    return all(
        group in destination_groups
        and _tree_contains(tree, destination_groups[group])
        for group, tree in source_groups.items()
    )


def panels_match(source: dict[str, Any], destination: dict[str, Any]) -> bool:
    """Compare a secondary panel with the primary panel profile."""

    if source["properties"] != destination["properties"]:
        return False
    source_widgets = source["widgets"]
    destination_widgets = destination["widgets"]
    if len(source_widgets) != len(destination_widgets):
        return False
    for expected, actual in zip(source_widgets, destination_widgets):
        if expected.get("type") != actual.get("type"):
            return False
        if expected.get("index") != actual.get("index"):
            return False
        if not _tree_contains(expected.get("config"), actual.get("config")):
            return False
        expected_tray = expected.get("trayConfig")
        if expected_tray is not None and not _tree_contains(
            expected_tray, actual.get("trayConfig")
        ):
            return False
    return True


def _apply_script(source: dict[str, Any], destinations: list[int]) -> str:
    profile = json.dumps(
        {"properties": source["properties"], "widgets": source["widgets"]},
        separators=(",", ":"),
    )
    panel_ids = json.dumps(destinations)
    return f"""
var profile = {profile};
var destinationIds = {panel_ids};

function writeTree(applet, tree, path) {{
    if (!tree) return;
    applet.currentConfigGroup = path;
    var keys = Object.keys(tree.keys || {{}});
    for (var i = 0; i < keys.length; ++i) {{
        if (keys[i] === "SystrayContainmentId") continue;
        applet.writeConfig(keys[i], tree.keys[keys[i]]);
    }}
    var groups = Object.keys(tree.groups || {{}});
    for (var j = 0; j < groups.length; ++j) {{
        writeTree(applet, tree.groups[groups[j]], path.concat([groups[j]]));
    }}
    applet.currentConfigGroup = [];
}}

function syncTray(widget, tree) {{
    if (!tree) return;
    widget.currentConfigGroup = [];
    var trayId = Number(widget.readConfig("SystrayContainmentId", 0));
    if (!trayId) return;
    var tray = desktopById(trayId);
    if (!tray) return;
    writeTree(tray, tree, []);
    tray.reloadConfig();
}}

for (var p = 0; p < destinationIds.length; ++p) {{
    var panel = panelById(destinationIds[p]);
    if (!panel) continue;
    var props = profile.properties;
    panel.location = props.location;
    panel.height = props.height;
    panel.lengthMode = props.lengthMode;
    panel.alignment = props.alignment;
    panel.offset = props.offset;
    panel.hiding = props.hiding;

    var widgets = panel.widgets().concat([]);
    var byId = {{}};
    for (var wi = 0; wi < widgets.length; ++wi) byId[widgets[wi].id] = widgets[wi];
    panel.currentConfigGroup = ["General"];
    var order = String(panel.readConfig("AppletOrder", "")).split(";");
    panel.currentConfigGroup = [];
    var ordered = [];
    for (var oi = 0; oi < order.length; ++oi) {{
        if (byId[order[oi]]) {{
            ordered.push(byId[order[oi]]);
            delete byId[order[oi]];
        }}
    }}
    for (var remaining in byId) ordered.push(byId[remaining]);
    widgets = ordered;
    var structureMatches = widgets.length === profile.widgets.length;
    if (structureMatches) {{
        for (var i = 0; i < widgets.length; ++i) {{
            if (widgets[i].type !== profile.widgets[i].type) {{
                structureMatches = false;
                break;
            }}
        }}
    }}
    if (!structureMatches) {{
        for (var r = widgets.length - 1; r >= 0; --r) widgets[r].remove();
        widgets = [];
        for (var a = 0; a < profile.widgets.length; ++a) {{
            widgets.push(panel.addWidget(profile.widgets[a].type));
        }}
    }}
    for (var w = 0; w < profile.widgets.length; ++w) {{
        var widget = widgets[w];
        var expected = profile.widgets[w];
        writeTree(widget, expected.config, []);
        syncTray(widget, expected.trayConfig);
        widget.reloadConfig();
    }}
    panel.currentConfigGroup = ["General"];
    panel.writeConfig("AppletOrder", widgets.map(function(widget) {{
        return widget.id;
    }}).join(";"));
    panel.currentConfigGroup = [];
}}
"""


def sync_primary_panel(
    client: PanelScriptClient,
    *,
    dry_run: bool = False,
) -> PanelSyncResult:
    """Make all secondary panels match the screen-zero primary panel."""

    panels = query_panels(client)
    primary = [panel for panel in panels if panel["screen"] == 0]
    if len(primary) != 1:
        raise ValueError(
            f"expected exactly one panel on primary screen 0; found {len(primary)}"
        )
    source = primary[0]
    destinations = [panel for panel in panels if panel["screen"] != 0]
    changed = [panel for panel in destinations if not panels_match(source, panel)]
    rebuilt = any(
        [widget.get("type") for widget in panel["widgets"]]
        != [widget.get("type") for widget in source["widgets"]]
        for panel in changed
    )
    if changed and not dry_run:
        originals = {panel["id"]: panel for panel in changed}
        client.evaluate(_apply_script(source, [panel["id"] for panel in changed]))
        verified = query_panels(client)
        verified_by_id = {panel["id"]: panel for panel in verified}
        failed = [
            panel_id
            for panel_id in originals
            if panel_id not in verified_by_id
            or not panels_match(source, verified_by_id[panel_id])
        ]
        if failed:
            for panel_id, original in originals.items():
                client.evaluate(_apply_script(original, [panel_id]))
            raise RuntimeError(
                "panel synchronization did not verify; restored secondary panels"
            )
    return PanelSyncResult(
        panels=len(destinations) + 1,
        changed=bool(changed),
        rebuilt=rebuilt,
    )


def backup_plasma_config(state_path: Path, *, keep: int = 10) -> Path:
    """Create a timestamped backup of Plasma's panel configuration."""

    source = Path.home() / ".config" / "plasma-org.kde.plasma.desktop-appletsrc"
    if not source.is_file():
        raise FileNotFoundError(f"Plasma configuration not found: {source}")
    backup_dir = state_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"plasma-appletsrc-{stamp}-{time.time_ns()}.bak"
    shutil.copy2(source, destination)
    backups = sorted(backup_dir.glob("plasma-appletsrc-*.bak"))
    for old_backup in backups[:-keep]:
        old_backup.unlink()
    return destination
