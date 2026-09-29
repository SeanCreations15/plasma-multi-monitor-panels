#!/bin/sh
set -eu

bin_path="${HOME}/.local/bin/panel-sync"
unit_path="${HOME}/.local/share/systemd/user/panel-sync.service"
state_dir="${HOME}/.local/state/panel-sync"
legacy_bin_path="${HOME}/.local/bin/plasma-taskbar-sync"
legacy_unit_path="${HOME}/.local/share/systemd/user/plasma-taskbar-sync.service"
legacy_old_unit_path="${HOME}/.config/systemd/user/plasma-taskbar-sync.service"
legacy_state_dir="${HOME}/.local/state/plasma-taskbar-sync"

usage() {
    echo "Usage: $0" >&2
    exit 2
}

[ "$#" -eq 0 ] || usage

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now plasma-taskbar-sync.service >/dev/null 2>&1 || true
    systemctl --user disable --now panel-sync.service >/dev/null 2>&1 || true
fi

rm -f -- \
    "${bin_path}" \
    "${unit_path}" \
    "${legacy_bin_path}" \
    "${legacy_unit_path}" \
    "${legacy_old_unit_path}"
rm -rf -- "${state_dir}" "${legacy_state_dir}"
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload >/dev/null 2>&1 || true
fi

echo "Removed the program, service, and saved sync state"
