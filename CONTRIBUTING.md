# Contributing

Thanks for helping with Plasma Taskbar Sync.

## Scope

Keep the project focused on pinned apps and their order across standard Plasma
task managers. Panel layouts, themes, and other widgets are outside its scope.

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
