#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir="${HOME}/.local/bin"
unit_dir="${HOME}/.local/share/systemd/user"
executable="${bin_dir}/plasma-taskbar-sync"
unit="${unit_dir}/plasma-taskbar-sync.service"
old_unit="${HOME}/.config/systemd/user/plasma-taskbar-sync.service"
start_service=1

usage() {
    echo "Usage: $0 [--no-start]" >&2
    exit 2
}

[ "$#" -le 1 ] || usage
case "${1:-}" in
    "") ;;
    --no-start) start_service=0 ;;
    *) usage ;;
esac

for command_name in python3 install mktemp; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Missing command: ${command_name}" >&2
        exit 1
    fi
done

if ! command -v kscreen-doctor >/dev/null 2>&1; then
    echo "Missing command: kscreen-doctor (install KDE's KScreen package)" >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
fi

if ! command -v qdbus6 >/dev/null 2>&1 &&
    ! command -v qdbus-qt6 >/dev/null 2>&1; then
    custom_qdbus="${PLASMA_TASKBAR_SYNC_QDBUS:-}"
    if [ "${start_service}" -ne 0 ] ||
        [ -z "${custom_qdbus}" ] ||
        ! command -v "${custom_qdbus}" >/dev/null 2>&1; then
        echo "Missing Qt 6 qdbus command (qdbus6 or qdbus-qt6)." >&2
        exit 1
    fi
fi

if ! command -v kbuildsycoca6 >/dev/null 2>&1; then
    echo "Missing command: kbuildsycoca6" >&2
    exit 1
fi

mkdir -p "${bin_dir}" "${unit_dir}"
temporary=$(mktemp "${bin_dir}/.plasma-taskbar-sync.XXXXXX")
trap 'rm -f -- "${temporary}"' EXIT HUP INT TERM

python3 "${project_dir}/tools/build_zipapp.py" \
    "${project_dir}/src" \
    "${temporary}"
chmod 755 "${temporary}"
"${temporary}" --version >/dev/null
mv -f -- "${temporary}" "${executable}"
install -m644 "${project_dir}/systemd/plasma-taskbar-sync.service" "${unit}"
rm -f -- "${old_unit}"

systemd_ready=0
if command -v systemctl >/dev/null 2>&1 &&
    systemctl --user daemon-reload >/dev/null 2>&1; then
    systemd_ready=1
    if [ "${start_service}" -eq 1 ]; then
        systemctl --user reenable plasma-taskbar-sync.service >/dev/null
        systemctl --user restart plasma-taskbar-sync.service
    fi
elif [ "${start_service}" -eq 1 ]; then
    echo "A systemd user session is not available." >&2
    echo "Run '${executable} watch' from session startup." >&2
fi

trap - EXIT HUP INT TERM

echo "Installed ${executable}"
if [ "${start_service}" -eq 1 ] && [ "${systemd_ready}" -eq 1 ]; then
    echo "Started plasma-taskbar-sync.service"
fi
echo "Check: plasma-taskbar-sync status"
