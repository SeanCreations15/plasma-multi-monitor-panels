# Plasma Multi-Monitor Panels

Automatically create and synchronize KDE Plasma panels across multiple
monitors.

The service detects connected displays, identifies KDE's primary display, and
uses an existing panel as the initial source. It creates a panel on each display
that does not already have one, then keeps their layouts synchronized.

Synchronized settings include:

- Panel position, size, alignment, length, and visibility mode
- Widget types, order, and configuration
- System Tray configuration
- Task Manager and Icons-only Task Manager pinned applications

Changes can be made on any synchronized panel. The edited panel becomes the
source for the other displays.

## Requirements

- KDE Plasma 6.3 or newer
- Two or more displays
- Python 3.10 or newer
- Qt 6 `qdbus` (`qdbus6` or `qdbus-qt6`)
- `kbuildsycoca6`
- `kscreen-doctor`
- Git, `install`, and `mktemp`
- Linux with `inotify`
- A systemd user session for automatic startup

These components are normally included with a KDE Plasma installation.

## Install

```sh
git clone https://github.com/SeanCreations15/plasma-multi-monitor-panels.git
cd plasma-multi-monitor-panels
./install.sh
```

No root access or third-party Python packages are required. The installer
installs the program as the `panel-sync` command and creates and starts:

```text
~/.local/bin/panel-sync
~/.local/share/systemd/user/panel-sync.service
```

When upgrading from the former `plasma-taskbar-sync` command, the installer
removes the old executable and service and moves its saved state and backups to
the new `panel-sync` state directory.

Check the installation:

```sh
panel-sync status
```

## Initial synchronization

The service maps KScreen outputs to Plasma screens using their logical geometry
and uses KScreen display priority to identify the primary display.

The initial source is selected as follows:

1. Use the existing panel on KDE's primary display.
2. If the primary display has no panel, use the existing panel on the
   next-highest-priority display.
3. Create missing panels and copy the source layout to them.

At least one connected display must already have a panel. The service will not
invent a layout when no source panel exists.

## Ongoing synchronization

The service watches Plasma's panel configuration and checks the complete layout
after each edit. It records the last layout shared by every display so it can
identify which panel changed.

If several panels receive the same change, that shared change is used. If they
receive conflicting changes simultaneously, the panel on KDE's primary display
wins. When the primary display has no panel, the panel on the next-highest-
priority display wins.

Newly connected monitors are detected automatically. A missing panel is created
and synchronized after Plasma and KScreen report a stable display layout.
Transient startup and hot-plug states are retried without modifying panels.

## Safety

Before writing, the service backs up Plasma's panel configuration to:

```text
~/.local/state/panel-sync/backups/
```

The ten newest backups are retained. Every panel update is read back and
verified. If verification fails, the previous destination layouts are restored
and newly created panels are removed.

The service makes no changes when:

- No connected display has an existing source panel
- A display has multiple panels, because the synchronization group is ambiguous
- Displays are mirrored, because they cannot be mapped safely to independent
  panels
- Plasma and KScreen disagree while the display layout is changing

## Commands

```text
panel-sync status [--json]
panel-sync doctor
panel-sync once [--dry-run]
panel-sync watch [--fallback-interval SECONDS]
panel-sync self-test
```

- `status` reports synchronization and service health.
- `doctor` prints display mapping, dependencies, panels, widgets, and launcher
  details.
- `once` performs one synchronization pass.
- `once --dry-run` checks whether changes are needed without writing anything.
- `watch` runs the continuous synchronization process.
- `self-test` temporarily changes pinned applications, verifies propagation,
  and restores the original values.

## Update

Run from the cloned repository:

```sh
git pull --ff-only
./install.sh
```

The installer replaces the executable and service unit, restarts the service,
and preserves saved synchronization state.

## Troubleshooting

```sh
panel-sync doctor
systemctl --user status panel-sync.service
journalctl --user -u panel-sync.service -n 100
```

The watcher logs synchronization changes, conflicts, display-mapping problems,
and retryable startup or hot-plug errors.

## Uninstall

Run from the cloned repository:

```sh
./uninstall.sh
```

This stops and disables the service and removes the installed executable,
service unit, process lock, and saved synchronization state. Existing Plasma
panels are not changed.

## License

Licensed under the Mozilla Public License 2.0. See [LICENSE](LICENSE). Copyright
and contribution details are recorded in [COPYRIGHT.md](COPYRIGHT.md).
