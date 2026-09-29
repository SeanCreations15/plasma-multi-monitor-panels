from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from panel_sync.core import (
    choose_launchers,
    effective_launchers,
    sync_once,
)
from panel_sync.model import TaskManager
from panel_sync.state import empty_state, read_state


def manager(widget: int, screen: int, launchers: str) -> TaskManager:
    return TaskManager(
        panel=screen + 10,
        screen=screen,
        widget=widget,
        plugin="org.kde.plasma.icontasks",
        root=launchers,
        general=launchers,
        launchers=launchers,
    )


def saved_state(selected: str, values: dict[int, str]) -> dict[str, object]:
    return {
        "version": 1,
        "selected": selected,
        "widgets": {
            str(widget): {
                "panel": widget + 10,
                "screen": widget,
                "plugin": "org.kde.plasma.icontasks",
                "effective": value,
            }
            for widget, value in values.items()
        },
    }


class FakeClient:
    def __init__(self, managers: list[TaskManager]) -> None:
        self.managers = managers
        self.writes: list[str] = []

    def task_managers(self, _state: object) -> list[TaskManager]:
        return self.managers

    def set_all_launchers(self, launchers: str) -> None:
        self.writes.append(launchers)
        self.managers = [
            replace(
                item,
                root=launchers,
                general=launchers,
                launchers=launchers,
            )
            for item in self.managers
        ]


class LauncherChoiceTests(unittest.TestCase):
    def test_first_sync_uses_screen_zero_even_when_not_first(self) -> None:
        decision = choose_launchers(
            [manager(2, 1, "a"), manager(1, 0, "")],
            empty_state(),
        )
        self.assertEqual(decision.launchers, "")
        self.assertFalse(decision.conflict)

    def test_first_sync_uses_mapped_primary_screen(self) -> None:
        decision = choose_launchers(
            [manager(1, 0, "secondary"), manager(2, 2, "primary")],
            empty_state(),
            primary_screen=2,
        )
        self.assertEqual(decision.launchers, "primary")

    def test_one_reorder_wins(self) -> None:
        old = saved_state("a,b", {1: "a,b", 2: "a,b"})
        decision = choose_launchers(
            [manager(1, 0, "a,b"), manager(2, 1, "b,a")],
            old,
        )
        self.assertEqual(decision.launchers, "b,a")
        self.assertFalse(decision.conflict)

    def test_one_pin_wins(self) -> None:
        old = saved_state("a", {1: "a", 2: "a"})
        decision = choose_launchers(
            [manager(1, 0, "a,b"), manager(2, 1, "a")],
            old,
        )
        self.assertEqual(decision.launchers, "a,b")

    def test_removing_final_launcher_wins(self) -> None:
        old = saved_state("a", {1: "a", 2: "a"})
        decision = choose_launchers(
            [manager(1, 0, "a"), manager(2, 1, "")],
            old,
        )
        self.assertEqual(decision.launchers, "")

    def test_conflict_uses_screen_zero(self) -> None:
        old = saved_state("a,b", {1: "a,b", 2: "a,b"})
        decision = choose_launchers(
            [manager(1, 0, "b,c"), manager(2, 1, "c,a")],
            old,
        )
        self.assertEqual(decision.launchers, "b,c")
        self.assertTrue(decision.conflict)

    def test_conflict_uses_mapped_primary_screen(self) -> None:
        old = saved_state("a", {1: "a", 2: "a"})
        decision = choose_launchers(
            [manager(1, 0, "secondary"), manager(2, 2, "primary")],
            old,
            primary_screen=2,
        )
        self.assertEqual(decision.launchers, "primary")
        self.assertTrue(decision.conflict)

    def test_conflict_without_screen_zero_uses_first_manager(self) -> None:
        old = saved_state("a", {1: "a", 2: "a"})
        decision = choose_launchers(
            [manager(1, 2, "b"), manager(2, 3, "c")],
            old,
        )
        self.assertEqual(decision.launchers, "b")
        self.assertTrue(decision.conflict)

    def test_new_task_manager_adopts_saved_list(self) -> None:
        old = saved_state("a", {1: "a"})
        decision = choose_launchers(
            [manager(1, 0, "a"), manager(2, 1, "new-default")],
            old,
        )
        self.assertEqual(decision.launchers, "a")
        self.assertFalse(decision.conflict)


class EffectiveLauncherTests(unittest.TestCase):
    def test_empty_root_edit_is_kept(self) -> None:
        self.assertEqual(effective_launchers("", "a", "a"), "")

    def test_empty_general_edit_is_kept(self) -> None:
        self.assertEqual(effective_launchers("a", "", "a"), "")

    def test_general_is_used_without_history(self) -> None:
        self.assertEqual(effective_launchers("a", "b", None), "b")

    def test_root_is_fallback_without_history(self) -> None:
        self.assertEqual(effective_launchers("a", "", None), "a")


class SyncOnceTests(unittest.TestCase):
    def test_sync_writes_plasma_and_state(self) -> None:
        client = FakeClient([manager(1, 0, "a"), manager(2, 1, "b")])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            result = sync_once(client, state_path)

            self.assertTrue(result.changed)
            self.assertEqual(client.writes, ["a"])
            state = read_state(state_path)
            self.assertEqual(state["selected"], "a")
            self.assertEqual(state["widgets"]["2"]["effective"], "a")

    def test_dry_run_writes_nothing(self) -> None:
        client = FakeClient([manager(1, 0, "a"), manager(2, 1, "b")])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            result = sync_once(client, state_path, dry_run=True)

            self.assertTrue(result.changed)
            self.assertEqual(client.writes, [])
            self.assertFalse(state_path.exists())

    def test_sync_reconciles_different_plasma_keys(self) -> None:
        stale = replace(manager(1, 0, "b"), root="a")
        client = FakeClient([stale, manager(2, 1, "b")])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            result = sync_once(client, state_path)

            self.assertTrue(result.changed)
            self.assertEqual(client.writes, ["b"])
            self.assertTrue(
                all(item.root == item.general == "b" for item in client.managers)
            )

    def test_no_managers_is_a_noop(self) -> None:
        client = FakeClient([])
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            result = sync_once(client, state_path)

            self.assertEqual(result.managers, 0)
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
