"""KScreen output discovery and Plasma screen mapping."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
from typing import Any, Protocol


SCREEN_QUERY_SCRIPT = r"""
var result = [];
for (var screen = 0; screen < screenCount; ++screen) {
    var geometry = screenGeometry(screen);
    result.push({
        screen: screen,
        x: geometry.x,
        y: geometry.y,
        width: geometry.width,
        height: geometry.height
    });
}
print(JSON.stringify(result));
"""


@dataclass(frozen=True)
class Geometry:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class DisplayOutput:
    """One enabled, connected KScreen output."""

    output_id: int
    name: str
    priority: int
    geometry: Geometry


@dataclass(frozen=True)
class PlasmaScreen:
    """One screen as numbered by the Plasma scripting API."""

    screen: int
    geometry: Geometry


@dataclass(frozen=True)
class MappedOutput:
    """A stable KScreen output mapped to a transient Plasma screen number."""

    output: DisplayOutput
    screen: int


@dataclass(frozen=True)
class DisplayLayout:
    """Current non-mirrored outputs in KDE priority order."""

    outputs: tuple[MappedOutput, ...]
    primary_screen: int

    @property
    def screens(self) -> tuple[int, ...]:
        return tuple(item.screen for item in self.outputs)

    def name_for_screen(self, screen: int) -> str | None:
        return next(
            (item.output.name for item in self.outputs if item.screen == screen),
            None,
        )


class ScriptClient(Protocol):
    def evaluate(self, script: str) -> str: ...


def find_kscreen_doctor() -> str:
    """Find KDE's command-line display configuration client."""

    resolved = shutil.which("kscreen-doctor")
    if resolved:
        return resolved
    raise FileNotFoundError(
        "kscreen-doctor was not found; install KDE's KScreen package"
    )


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"KScreen returned an invalid {label}")
    return round(value)


def _geometry(value: Any, label: str) -> Geometry:
    if not isinstance(value, dict):
        raise ValueError(f"KScreen returned an invalid {label}")
    return Geometry(
        x=_integer(value.get("x"), f"{label} x coordinate"),
        y=_integer(value.get("y"), f"{label} y coordinate"),
        width=_integer(value.get("width"), f"{label} width"),
        height=_integer(value.get("height"), f"{label} height"),
    )


def parse_kscreen_outputs(payload: str) -> list[DisplayOutput]:
    """Parse enabled physical outputs from ``kscreen-doctor -j``."""

    value = json.loads(payload or "{}")
    records = value.get("outputs") if isinstance(value, dict) else None
    if not isinstance(records, list):
        raise ValueError("KScreen returned invalid output data")

    outputs: list[DisplayOutput] = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("KScreen returned an invalid output record")
        if not record.get("connected") or not record.get("enabled"):
            continue
        # A panel cannot be assigned reliably to two outputs with identical
        # logical geometry. Refuse the topology instead of creating duplicates.
        replication_source = record.get("replicationSource", 0)
        if isinstance(replication_source, (int, float)) and replication_source:
            raise ValueError(
                "mirrored KScreen outputs are not supported; disable display "
                "replication before synchronizing panels"
            )

        modes = record.get("modes")
        current_mode = str(record.get("currentModeId", ""))
        if not isinstance(modes, list) or not current_mode:
            raise ValueError("KScreen output has no active mode")
        mode = next(
            (
                candidate
                for candidate in modes
                if isinstance(candidate, dict)
                and str(candidate.get("id", "")) == current_mode
            ),
            None,
        )
        if not isinstance(mode, dict):
            raise ValueError("KScreen output active mode was not found")
        size = mode.get("size")
        position = record.get("pos")
        if not isinstance(size, dict) or not isinstance(position, dict):
            raise ValueError("KScreen output has invalid geometry")
        scale = record.get("scale", 1)
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
            raise ValueError("KScreen output has an invalid scale")
        width = _integer(size.get("width"), "output width")
        height = _integer(size.get("height"), "output height")
        rotation = record.get("rotation", 1)
        if rotation in (2, 8):
            width, height = height, width

        output_id = record.get("id")
        priority = record.get("priority", 0)
        name = record.get("name")
        if isinstance(output_id, bool) or not isinstance(output_id, int):
            raise ValueError("KScreen output has an invalid id")
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("KScreen output has an invalid priority")
        if not isinstance(name, str) or not name:
            raise ValueError("KScreen output has an invalid name")
        outputs.append(
            DisplayOutput(
                output_id=output_id,
                name=name,
                priority=priority,
                geometry=Geometry(
                    x=_integer(position.get("x"), "output x coordinate"),
                    y=_integer(position.get("y"), "output y coordinate"),
                    width=round(width / scale),
                    height=round(height / scale),
                ),
            )
        )
    if not outputs:
        raise RuntimeError("KScreen reports no enabled connected outputs")
    return outputs


def query_plasma_screens(client: ScriptClient) -> list[PlasmaScreen]:
    """Read screen numbers and logical geometry from Plasma."""

    value = json.loads(client.evaluate(SCREEN_QUERY_SCRIPT) or "[]")
    if not isinstance(value, list):
        raise ValueError("Plasma returned invalid screen data")
    screens: list[PlasmaScreen] = []
    for record in value:
        if not isinstance(record, dict):
            raise ValueError("Plasma returned an invalid screen record")
        screen = record.get("screen")
        if isinstance(screen, bool) or not isinstance(screen, int):
            raise ValueError("Plasma returned an invalid screen number")
        screens.append(
            PlasmaScreen(
                screen=screen,
                geometry=_geometry(record, "screen geometry"),
            )
        )
    if not screens:
        raise RuntimeError("Plasma reports no screens")
    return screens


def _geometry_distance(first: Geometry, second: Geometry) -> int:
    return sum(
        abs(left - right)
        for left, right in zip(
            (first.x, first.y, first.width, first.height),
            (second.x, second.y, second.width, second.height),
        )
    )


def map_outputs_to_screens(
    outputs: list[DisplayOutput],
    screens: list[PlasmaScreen],
    *,
    tolerance: int = 4,
) -> DisplayLayout:
    """Map KScreen outputs to Plasma screen numbers by logical geometry."""

    if len(outputs) != len(screens):
        raise RuntimeError(
            "display configuration is still settling: "
            f"KScreen reports {len(outputs)} outputs but Plasma reports "
            f"{len(screens)} screens"
        )

    available = {screen.screen: screen for screen in screens}
    mapped: list[MappedOutput] = []
    for output in sorted(outputs, key=lambda item: item.output_id):
        candidates = sorted(
            (
                (_geometry_distance(output.geometry, screen.geometry), screen)
                for screen in available.values()
            ),
            key=lambda item: (item[0], item[1].screen),
        )
        if not candidates or candidates[0][0] > tolerance:
            raise RuntimeError(
                "display configuration is still settling: could not map "
                f"KScreen output {output.name} to a Plasma screen"
            )
        distance, screen = candidates[0]
        if len(candidates) > 1 and candidates[1][0] == distance:
            raise RuntimeError(
                f"ambiguous Plasma screen mapping for output {output.name}"
            )
        mapped.append(MappedOutput(output=output, screen=screen.screen))
        del available[screen.screen]

    mapped.sort(
        key=lambda item: (
            item.output.priority if item.output.priority > 0 else 1_000_000,
            item.output.output_id,
        )
    )
    primary = next(
        (item for item in mapped if item.output.priority == 1),
        mapped[0],
    )
    return DisplayLayout(tuple(mapped), primary.screen)


def discover_display_layout(
    client: ScriptClient,
    kscreen_doctor: str,
    *,
    timeout: float = 8,
) -> DisplayLayout:
    """Discover and reconcile KScreen outputs with Plasma screens."""

    result = subprocess.run(
        [kscreen_doctor, "-j"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    return map_outputs_to_screens(
        parse_kscreen_outputs(result.stdout),
        query_plasma_screens(client),
    )
