# Contributing

Thanks for helping with Plasma Multi-Monitor Panels.

## Scope

Keep the project focused on creating and synchronizing standard Plasma panels
across connected displays. Panel properties, widget layouts, System Tray
configuration, and task-manager launchers are in scope. Desktop themes,
wallpapers, and general display configuration are outside its scope.

The project has one install path: clone the repository and run `./install.sh`.
Do not add Python package metadata, release archives, downloaded dependencies,
or a persistent build directory.

## Bug reports

Include:

- `plasma-taskbar-sync doctor`
- the Plasma version
- the distro and release
- the task manager type
- the relevant service logs

Remove private app names from the launcher list if needed.

## Making a change

- Put sync choices in `core.py`.
- Put KScreen discovery and output mapping in `displays.py`.
- Put complete-panel snapshot and replication logic in `panels.py`.
- Put Plasma reads, writes, and refresh calls in `plasma.py`.
- Put Linux file-event handling in `events.py`.
- Keep command output and argument handling in `cli.py`.
- Keep zipapp build details in `tools/build_zipapp.py`.
- Keep state files backward compatible or raise the state version.
- Add a focused test for each behavior change.
- Make live tests restore every setting, even after a failure.

The `kbuildsycoca6` refresh is deliberate. Plasma can save a launcher change
without updating the task manager's live view. Run the refresh only after a
write.

Use only Python's standard library. Do not add distro-specific install commands
to the app.

## Checks

Run:

```sh
./check.sh
```

The install test uses a temporary home directory and a fake `systemctl`. The
event tests use temporary directories and the host's `inotify` support. Keep
tests isolated from the user's files, service, and Plasma session.
