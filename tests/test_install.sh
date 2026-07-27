#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d -t plasma-taskbar-sync-install.XXXXXX)
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

for command_name in qdbus6 kbuildsycoca6; do
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

old_unit_dir="${test_home}/.config/systemd/user"
old_unit="${old_unit_dir}/plasma-taskbar-sync.service"
mkdir -p "${old_unit_dir}"
: > "${old_unit}"

./install.sh --no-start >/dev/null
executable="${test_home}/.local/bin/plasma-taskbar-sync"
unit="${test_home}/.local/share/systemd/user/plasma-taskbar-sync.service"
[ -x "${executable}" ]
[ -f "${unit}" ]
[ ! -e "${old_unit}" ]
"${executable}" --version >/dev/null
if grep -Eq -- '--user (enable|reenable|restart)' "${systemctl_log}"; then
    echo "install.sh --no-start started the service" >&2
    exit 1
fi

: > "${systemctl_log}"
./install.sh >/dev/null
grep -q -- '--user reenable plasma-taskbar-sync.service' "${systemctl_log}"
grep -q -- '--user restart plasma-taskbar-sync.service' "${systemctl_log}"

state_dir="${test_home}/.local/state/plasma-taskbar-sync"
mkdir -p "${state_dir}"
: > "${state_dir}/state.json"
: > "${state_dir}/sync.lock"
: > "${old_unit}"

: > "${systemctl_log}"
./uninstall.sh >/dev/null
[ ! -e "${executable}" ]
[ ! -e "${unit}" ]
[ ! -e "${old_unit}" ]
[ ! -e "${state_dir}" ]
grep -q -- '--user disable --now plasma-taskbar-sync.service' "${systemctl_log}"
grep -q -- '--user daemon-reload' "${systemctl_log}"

if ./uninstall.sh --purge >/dev/null 2>&1; then
    echo "uninstall.sh still accepts --purge" >&2
    exit 1
fi

trap - EXIT HUP INT TERM
rm -rf -- "${test_root}"
