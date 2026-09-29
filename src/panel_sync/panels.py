"""Three-way KDE Plasma panel synchronization."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import time
from typing import Any, Protocol

from panel_sync.displays import (
    DisplayLayout,
    DisplayOutput,
    Geometry,
    MappedOutput,
)


PANEL_QUERY_SCRIPT = r"""
function readTree(applet, path) {
    applet.currentConfigGroup = path;
    var result = {keys: {}, groups: {}};
    var ignoredKeys = {
        activityId: true, formfactor: true, immutability: true,
        lastScreen: true, location: true, plugin: true,
        popupHeight: true, popupWidth: true,
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
        if (groups[j] === "Applets" || groups[j] === "ConfigDialog"
                || groups[j] === "Shortcuts") continue;
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
        var config = readTree(widget, []);
        // Digital Clock removes dateDisplayFormat from KConfig when the user
        // selects its default, Adaptive. Preserve that semantic default in
        // the snapshot so it can be copied over an explicitly stored value
        // such as BesideTime on another panel.
        if (widget.type === "org.kde.plasma.digitalclock") {
            widget.currentConfigGroup = ["Appearance"];
            if (!config.groups.Appearance) {
                config.groups.Appearance = {keys: {}, groups: {}};
            }
            config.groups.Appearance.keys.dateDisplayFormat = String(
                widget.readConfig("dateDisplayFormat", "Adaptive") || "Adaptive"
            );
            widget.currentConfigGroup = [];
        }
        widgetResult.push({
            type: widget.type,
            index: j,
            config: config,
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
    source_screen: int = 0
    conflict: bool = False
    created: int = 0


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


def panels_equivalent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Compare profiles in both directions so newly added keys are detected."""

    return panels_match(first, second) and panels_match(second, first)


def _profile(panel: dict[str, Any]) -> dict[str, Any]:
    return {
        "properties": panel["properties"],
        "widgets": panel["widgets"],
    }


def _panel_state_path(state_path: Path) -> Path:
    return state_path.with_name("panels.json")


def _valid_profile(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("properties"), dict)
        and isinstance(value.get("widgets"), list)
    )


def _read_panel_state(state_path: Path) -> dict[int, dict[str, Any]] | None:
    path = _panel_state_path(state_path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"unsupported panel sync state in {path}")
    # Version 1 stored one universal profile. Plasma may serialize an
    # equivalent default as an omitted key on one panel and an explicit key on
    # another, so a universal baseline creates false conflicts. Treat it as
    # uninitialized and migrate it on the next successful pass.
    if value.get("version") == 1:
        return None
    if value.get("version") != 2 or not isinstance(value.get("screens"), dict):
        raise ValueError(f"unsupported panel sync state in {path}")
    profiles: dict[int, dict[str, Any]] = {}
    for screen, profile in value["screens"].items():
        try:
            screen_number = int(screen)
        except (TypeError, ValueError) as error:
            raise ValueError(f"invalid panel screen in {path}") from error
        if not _valid_profile(profile):
            raise ValueError(f"invalid panel profile for screen {screen} in {path}")
        profiles[screen_number] = profile
    return profiles


def _write_panel_state(
    state_path: Path, panels: list[dict[str, Any]]
) -> None:
    path = _panel_state_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "version": 2,
                    "screens": {
                        str(panel["screen"]): _profile(panel)
                        for panel in panels
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
        var value = tree.keys[keys[i]];
        // KConfig exposes StringList values as comma-joined strings through
        // Plasma's scripting API. Writing that string back makes Task Manager
        // treat the whole list as one malformed launcher, so restore its
        // actual array type before writing.
        if (keys[i] === "launchers" && typeof value === "string") {{
            value = value === "" ? [] : value.split(",").filter(function(item) {{
                return item !== "";
            }});
        }}
        applet.writeConfig(keys[i], value);
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


def _create_panels_script(screens: list[int]) -> str:
    targets = json.dumps(screens)
    return f"""
var targetScreens = {targets};
var created = [];
for (var i = 0; i < targetScreens.length; ++i) {{
    var panel = new Panel;
    panel.screen = targetScreens[i];
    created.push(panel.id);
}}
print(JSON.stringify(created));
"""


def _remove_panels_script(panel_ids: list[int]) -> str:
    targets = json.dumps(panel_ids)
    return f"""
var targetPanels = {targets};
for (var i = 0; i < targetPanels.length; ++i) {{
    var panel = panelById(targetPanels[i]);
    if (panel) panel.remove();
}}
"""


def _active_panels(
    panels: list[dict[str, Any]], layout: DisplayLayout
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    active_screens = set(layout.screens)
    active = [panel for panel in panels if panel["screen"] in active_screens]
    by_screen: dict[int, dict[str, Any]] = {}
    for panel in active:
        screen = panel["screen"]
        if screen in by_screen:
            name = layout.name_for_screen(screen) or f"screen {screen}"
            raise ValueError(
                f"multiple panels found on {name}; automatic panel grouping "
                "is not supported yet"
            )
        by_screen[screen] = panel
    return active, by_screen


def _fallback_source(
    by_screen: dict[int, dict[str, Any]], layout: DisplayLayout
) -> dict[str, Any]:
    primary = by_screen.get(layout.primary_screen)
    if primary is not None:
        return primary
    for output in layout.outputs:
        panel = by_screen.get(output.screen)
        if panel is not None:
            return panel
    raise ValueError(
        "no existing panel was found on any connected output; add one Plasma "
        "panel and retry"
    )


def sync_panels(
    client: PanelScriptClient,
    state_path: Path,
    layout: DisplayLayout | None = None,
    *,
    dry_run: bool = False,
) -> PanelSyncResult:
    """Propagate the panel changed since the last agreed snapshot."""

    panels = query_panels(client)
    if layout is None:
        screens = sorted({panel["screen"] for panel in panels if panel["screen"] >= 0})
        if not screens:
            raise ValueError("no Plasma panels found")
        # Compatibility path for callers that do not perform KScreen mapping.
        layout = DisplayLayout(
            tuple(
                MappedOutput(
                    DisplayOutput(
                        screen,
                        f"screen-{screen}",
                        screen + 1,
                        Geometry(0, 0, 1, 1),
                    ),
                    screen,
                )
                for screen in screens
            ),
            0 if 0 in screens else screens[0],
        )
    active, by_screen = _active_panels(panels, layout)
    source = _fallback_source(by_screen, layout)
    previous = _read_panel_state(state_path)
    conflict = False
    if previous is not None:
        edited = [
            panel
            for panel in active
            if panel["screen"] in previous
            and not panels_equivalent(
                previous[panel["screen"]], _profile(panel)
            )
        ]
        if not edited:
            source = _fallback_source(by_screen, layout)
        elif len(edited) == 1:
            source = edited[0]
        elif all(
            panels_equivalent(_profile(edited[0]), _profile(panel))
            for panel in edited[1:]
        ):
            source = edited[0]
        else:
            source = _fallback_source(by_screen, layout)
            conflict = True

    missing_screens = [
        screen for screen in layout.screens if screen not in by_screen
    ]
    created_ids: list[int] = []
    if missing_screens and not dry_run:
        original_ids = {panel["id"] for panel in panels}
        client.evaluate(_create_panels_script(missing_screens))
        panels = query_panels(client)
        created_ids = [
            panel["id"] for panel in panels if panel["id"] not in original_ids
        ]
        try:
            active, by_screen = _active_panels(panels, layout)
            still_missing = [
                screen for screen in layout.screens if screen not in by_screen
            ]
            if still_missing or len(created_ids) != len(missing_screens):
                raise RuntimeError(
                    "Plasma did not create exactly one panel on every missing output"
                )
        except (RuntimeError, ValueError):
            if created_ids:
                client.evaluate(_remove_panels_script(created_ids))
            raise

    destinations = [panel for panel in active if panel["id"] != source["id"]]
    changed = [panel for panel in destinations if not panels_match(source, panel)]
    rebuilt = any(
        [widget.get("type") for widget in panel["widgets"]]
        != [widget.get("type") for widget in source["widgets"]]
        for panel in changed
    )
    if changed and not dry_run:
        originals = {
            panel["id"]: panel
            for panel in changed
            if panel["id"] not in created_ids
        }
        client.evaluate(_apply_script(source, [panel["id"] for panel in changed]))
        verified = query_panels(client)
        verified_by_id = {panel["id"]: panel for panel in verified}
        destination_ids = [panel["id"] for panel in changed]
        failed = [
            panel_id
            for panel_id in destination_ids
            if panel_id not in verified_by_id
            or not panels_match(source, verified_by_id[panel_id])
        ]
        if failed:
            for panel_id, original in originals.items():
                client.evaluate(_apply_script(original, [panel_id]))
            if created_ids:
                client.evaluate(_remove_panels_script(created_ids))
            raise RuntimeError(
                "panel synchronization did not verify; restored destination panels"
            )
        active, _ = _active_panels(verified, layout)
    if not dry_run:
        _write_panel_state(state_path, active)
    return PanelSyncResult(
        panels=len(layout.screens),
        changed=bool(changed or missing_screens),
        rebuilt=rebuilt,
        source_screen=source["screen"],
        conflict=conflict,
        created=len(missing_screens),
    )


def sync_primary_panel(
    client: PanelScriptClient,
    *,
    dry_run: bool = False,
) -> PanelSyncResult:
    """Compatibility wrapper for callers that do not persist panel history."""

    panels = query_panels(client)
    primary = [panel for panel in panels if panel["screen"] == 0]
    if len(primary) != 1:
        raise ValueError(
            f"expected exactly one panel on primary screen 0; found {len(primary)}"
        )
    source = primary[0]
    destinations = [panel for panel in panels if panel["id"] != source["id"]]
    changed = [panel for panel in destinations if not panels_match(source, panel)]
    if changed and not dry_run:
        client.evaluate(_apply_script(source, [panel["id"] for panel in changed]))
    return PanelSyncResult(len(panels), bool(changed))


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
