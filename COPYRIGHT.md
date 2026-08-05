# Copyright and authorship

This project contains work contributed by two copyright holders. The notices
below describe the scope of their respective contributions; they do not imply
equal authorship or ownership of the project as it exists today.

## SleepyStew contribution

Copyright (c) 2026 SleepyStew

SleepyStew's copyright applies to the code and documentation present in the
inherited revisions `a08ff47`, `4932e75`, and `12bcb31`, to the extent that
material remains in this project.

That inherited program synchronized the pinned application launcher list and
its ordering between Task Manager widgets on already-existing Plasma panels.
It also included the supporting command-line interface, saved-state handling,
Plasma configuration access, event watcher, installer, service definition, and
tests needed for that launcher-list synchronization.

The inherited program did **not** create panels, synchronize complete panel
layouts, map panels reliably to physical monitor connectors, determine the
actual KDE primary display, or provide the present startup and monitor
hot-plug safeguards.

## SeanCreations15 contribution

Copyright (c) 2026 SeanCreations15

SeanCreations15's copyright applies to modifications and additions beginning
with commit `1e1f10d`, as well as any future additions authored by
SeanCreations15. These contributions expanded the project from launcher-list
synchronization into multi-monitor panel synchronization, including complete
panel profiles, panel creation, display discovery and mapping, primary-display
selection, startup and hot-plug safety, verification and rollback behavior,
and the related documentation and tests.

The following source and test files were first added by SeanCreations15 and are
not present in the inherited SleepyStew revision (`12bcb31`):

- `src/plasma_taskbar_sync/displays.py`
- `src/plasma_taskbar_sync/panels.py`
- `tests/test_displays.py`
- `tests/test_panels.py`

Files that existed at revision `12bcb31` and were subsequently changed can
contain contributions from both copyright holders. SleepyStew's notice covers
only their retained expression in those files; it does not confer ownership of
SeanCreations15's later modifications, the newly added files, or the project
name. Git history is the detailed record of authorship.

All covered source code remains licensed under the Mozilla Public License 2.0.
See [LICENSE](LICENSE).
