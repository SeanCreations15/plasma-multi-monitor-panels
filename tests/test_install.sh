#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d -t panel-sync-install.XXXXXX)
trap 'rm -rf -- "${test_root}"' EXIT HUP INT TERM

test_home="${test_root}/home"
test_bin="${test_root}/bin"
systemctl_log="${test_root}/systemctl.log"
mkdir -p "${test_home}" "${test_bin}"

cat > "${test_bin}/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${SYSTEMCTL_LOG}"
EOF
chmod 755 "${test_bin}/systemctl"

for command_name in qdbus6 kbuildsycoca6 kscreen-doctor; do
    cat > "${test_bin}/${command_name}" <<'EOF'
#!/bin/sh
exit 0
EOF
    chmod 755 "${test_bin}/${command_name}"
done

export HOME="${test_home}"
export SYSTEMCTL_LOG="${systemctl_log}"
PATH="${test_bin}:${PATH}"
export PATH

cd "${project_dir}"

legacy_bin="${test_home}/.local/bin/plasma-taskbar-sync"
legacy_unit="${test_home}/.local/share/systemd/user/plasma-taskbar-sync.service"
legacy_old_unit="${test_home}/.config/systemd/user/plasma-taskbar-sync.service"
legacy_state_dir="${test_home}/.local/state/plasma-taskbar-sync"
mkdir -p \
    "$(dirname -- "${legacy_bin}")" \
    "$(dirname -- "${legacy_unit}")" \
    "$(dirname -- "${legacy_old_unit}")" \
    "${legacy_state_dir}/backups"
: > "${legacy_bin}"
: > "${legacy_unit}"
: > "${legacy_old_unit}"
: > "${legacy_state_dir}/state.json"
: > "${legacy_state_dir}/backups/existing-backup"

./install.sh --no-start >/dev/null
executable="${test_home}/.local/bin/panel-sync"
unit="${test_home}/.local/share/systemd/user/panel-sync.service"
[ -x "${executable}" ]
[ -f "${unit}" ]
[ ! -e "${legacy_bin}" ]
[ ! -e "${legacy_unit}" ]
[ ! -e "${legacy_old_unit}" ]
[ ! -e "${legacy_state_dir}" ]
[ -f "${test_home}/.local/state/panel-sync/state.json" ]
[ -f "${test_home}/.local/state/panel-sync/backups/existing-backup" ]
"${executable}" --version >/dev/null
if grep -Eq -- '--user (enable|reenable|restart)' "${systemctl_log}"; then
    echo "install.sh --no-start started the service" >&2
    exit 1
fi

: > "${systemctl_log}"
./install.sh >/dev/null
grep -q -- '--user reenable panel-sync.service' "${systemctl_log}"
grep -q -- '--user restart panel-sync.service' "${systemctl_log}"

state_dir="${test_home}/.local/state/panel-sync"
: > "${state_dir}/state.json"
: > "${state_dir}/sync.lock"
: > "${legacy_bin}"
: > "${legacy_unit}"
: > "${legacy_old_unit}"
mkdir -p "${legacy_state_dir}"
: > "${legacy_state_dir}/state.json"

: > "${systemctl_log}"
./uninstall.sh >/dev/null
[ ! -e "${executable}" ]
[ ! -e "${unit}" ]
[ ! -e "${state_dir}" ]
[ ! -e "${legacy_bin}" ]
[ ! -e "${legacy_unit}" ]
[ ! -e "${legacy_old_unit}" ]
[ ! -e "${legacy_state_dir}" ]
grep -q -- '--user disable --now panel-sync.service' "${systemctl_log}"
grep -q -- '--user disable --now plasma-taskbar-sync.service' "${systemctl_log}"
grep -q -- '--user daemon-reload' "${systemctl_log}"

if ./uninstall.sh --purge >/dev/null 2>&1; then
    echo "uninstall.sh still accepts --purge" >&2
    exit 1
fi

trap - EXIT HUP INT TERM
rm -rf -- "${test_root}"
