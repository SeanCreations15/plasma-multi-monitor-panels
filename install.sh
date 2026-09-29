#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
bin_dir="${HOME}/.local/bin"
unit_dir="${HOME}/.local/share/systemd/user"
executable="${bin_dir}/panel-sync"
unit="${unit_dir}/panel-sync.service"
legacy_executable="${bin_dir}/plasma-taskbar-sync"
legacy_unit="${unit_dir}/plasma-taskbar-sync.service"
legacy_old_unit="${HOME}/.config/systemd/user/plasma-taskbar-sync.service"
state_dir="${HOME}/.local/state/panel-sync"
legacy_state_dir="${HOME}/.local/state/plasma-taskbar-sync"
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
    custom_qdbus="${PANEL_SYNC_QDBUS:-${PLASMA_TASKBAR_SYNC_QDBUS:-}}"
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
temporary=$(mktemp "${bin_dir}/.panel-sync.XXXXXX")
trap 'rm -f -- "${temporary}"' EXIT HUP INT TERM

python3 "${project_dir}/tools/build_zipapp.py" \
    "${project_dir}/src" \
    "${temporary}"
chmod 755 "${temporary}"
"${temporary}" --version >/dev/null
mv -f -- "${temporary}" "${executable}"
install -m644 "${project_dir}/systemd/panel-sync.service" "${unit}"

# Preserve state and backups when upgrading from the former public name.
if [ ! -e "${state_dir}" ] && [ -d "${legacy_state_dir}" ]; then
    mv -- "${legacy_state_dir}" "${state_dir}"
fi

systemd_ready=0
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now plasma-taskbar-sync.service >/dev/null 2>&1 || true
fi
rm -f -- "${legacy_executable}" "${legacy_unit}" "${legacy_old_unit}"

if command -v systemctl >/dev/null 2>&1 &&
    systemctl --user daemon-reload >/dev/null 2>&1; then
    systemd_ready=1
    if [ "${start_service}" -eq 1 ]; then
        systemctl --user reenable panel-sync.service >/dev/null
        systemctl --user restart panel-sync.service
    fi
elif [ "${start_service}" -eq 1 ]; then
    echo "A systemd user session is not available." >&2
    echo "Run '${executable} watch' from session startup." >&2
fi

trap - EXIT HUP INT TERM

echo "Installed ${executable}"
if [ "${start_service}" -eq 1 ] && [ "${systemd_ready}" -eq 1 ]; then
    echo "Started panel-sync.service"
fi
echo "Check: panel-sync status"
