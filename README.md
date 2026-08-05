# Plasma Multi-Monitor Panels

Keep complete KDE Plasma panels synchronized across multiple displays.

The service detects KDE's connected displays and primary output. On first run,
it uses the existing panel on the primary display as the source, creates panels
on displays that do not have one, and copies the complete panel layout to them.
Afterwards, add, remove, configure, or move a panel widget on any synchronized
display and the change is copied to every other display. Panel layout and
task-manager launchers are synchronized too.

Before changing panels, the service stores timestamped configuration backups
under `~/.local/state/plasma-taskbar-sync/backups/` and retains the ten newest.
Every update is read back from Plasma for verification; if verification fails,
the service attempts to restore the prior secondary-panel snapshots.

## Install

```sh
git clone https://github.com/SeanCreations15/plasma-multi-monitor-panels.git \
&& cd plasma-multi-monitor-panels \
&& ./install.sh
```

The installer builds a Python zipapp from the cloned source. It has no
third-party Python dependencies.

It installs two files:

- `~/.local/bin/plasma-taskbar-sync`
- `~/.local/share/systemd/user/plasma-taskbar-sync.service`

It then enables and starts the systemd user service. No root access is needed.
Upgrades remove the old unit automatically.

Check the setup:

```sh
plasma-taskbar-sync status
```

## Project history

This repository is a maintained fork of
[SleepyStew/plasma-taskbar-sync](https://github.com/SleepyStew/plasma-taskbar-sync).
It extends the original task-manager synchronization utility to keep complete
panels synchronized and to accept edits made on any display.

The project exists because Plasma does not provide a built-in way to maintain
the same panel layout and pinned applications across multiple monitors.

## First run

The service waits for Plasma and KScreen to report the same stable display
layout before making changes. It maps Plasma's temporary screen numbers to
KScreen connector names using logical screen geometry, then uses KScreen's
display priority to find the real primary output.

- If the primary display already has a panel, that panel is the initial source.
- If the primary display has no panel, the existing panel on the next-highest
  priority display becomes the source.
- Missing panels are created and assigned to their displays before the source
  layout is applied.
- If no connected display has a panel, the service makes no changes and asks
  the user to add one panel first.
- If a display has multiple panels, the service makes no changes because
  choosing a synchronization group would be ambiguous.
- Mirrored display configurations are left unchanged because they do not map
  safely to independent Plasma panels.

The service backs up Plasma's configuration before creating or updating any
panel. A newly connected display is handled by the same process. During Plasma
startup or monitor hot-plug transitions, incomplete or inconsistent display
data is retried without changing panels.

## Requirements

- KDE Plasma 6.3 or newer
- Two or more displays with a standard Task Manager or Icons-only Task Manager
- Python 3.10 or newer
- Qt 6 `qdbus`, named `qdbus6` or `qdbus-qt6`
- KDE's `kbuildsycoca6` tool
- KDE's `kscreen-doctor` tool
- Git, `install`, and `mktemp`
- Linux with `inotify`
- A systemd user session for automatic startup

These dependencies come pre-installed on most Plasma distros.

## Commands

```text
plasma-taskbar-sync doctor
plasma-taskbar-sync status [--json]
plasma-taskbar-sync once [--dry-run]
plasma-taskbar-sync watch [--fallback-interval SECONDS]
plasma-taskbar-sync self-test
```

- `doctor` prints full setup, service, Plasma, and taskbar details.
- `status` reports whether the taskbars match and the service is running.
- `once` runs one sync and exits.
- `watch` reacts to taskbar changes until stopped.
- `self-test` tests pinning, order, and an empty list against live taskbars.

The self-test changes one taskbar, checks that the change reaches all taskbars,
then restores each taskbar and the prior state file.

## Update

From the cloned project:

```sh
git pull --ff-only && ./install.sh
```

The installer replaces the executable and service file, restarts the service,
and keeps the saved sync state.

## How it works

The watcher listens for changes to Plasma's applet settings, waits half a
second for each edit to finish, then rediscovers the connected outputs and
compares every panel with the last layout on which all displays agreed. It also
runs a safety check every 60 seconds.

The panel synchronizer remembers the last layout agreed by all displays. It
compares each live panel with that snapshot to identify which display changed,
then uses that panel as the source for the others. Snapshots include panel
properties, widget types, widget order, recursive widget configuration, and
System Tray settings. Monitor-specific containment metadata and global
shortcuts are deliberately excluded. Existing matching widgets are updated in
place so their Plasma instance IDs remain stable; a panel is rebuilt only when
its widget structure differs.

If `inotify` is not available, the service checks every two seconds and tries
to restore the event watch once a minute.

One edited panel wins regardless of its display. If multiple panels receive
the same edit before a sync pass, that shared edit wins. If multiple panels
receive incompatible edits at the same time, the panel on KDE's primary
display wins as the deterministic conflict fallback. If the primary display
has no panel, the panel on the connected output with the next-highest priority
wins.

Plasma stores launcher settings separately from the task manager's live view.
After a write, the app runs `kbuildsycoca6 --noincremental`. The cache event
prompts Plasma to redraw the launcher list at once. This runs only after a real
change, not on every check.

## Files and saved state

The app stores its state and process lock in:

```text
~/.local/state/plasma-taskbar-sync/
```

`panels.json` contains the last layout agreed by all panels and is what makes
changes from secondary displays distinguishable from stale layouts.

## Troubleshooting

Check taskbar and service health:

```sh
plasma-taskbar-sync status
```

Print full diagnostic information:

```sh
plasma-taskbar-sync doctor
```

`doctor` includes command paths, versions, file paths, service state, widget
IDs, plugin names, config-key state, and launcher lists.

Check the service and recent logs:

```sh
systemctl --user status plasma-taskbar-sync.service
journalctl --user -u plasma-taskbar-sync.service -n 100
```

The watcher stays quiet when nothing changes. It logs writes, conflicts,
errors, and event-watch failures.

## Uninstall

From the cloned project:

```sh
./uninstall.sh
```

This stops and disables the service, then removes the executable, service file,
process lock, and saved state. It does not change the current taskbars.
It also removes the old service path used by earlier versions.

The script does not remove a custom file passed through `--state-file`.

## Development

Run all local checks:

```sh
./check.sh
```

This runs the Python tests, checks every shell script, tests install and
uninstall in temporary user directories, builds a temporary zipapp, and checks
the systemd unit when the host allows it.

See [CONTRIBUTING](CONTRIBUTING.md) for more information.

## License

Licensed under the Mozilla Public License 2.0. See [LICENSE](LICENSE).
