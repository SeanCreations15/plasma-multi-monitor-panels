#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "${project_dir}"

for script in check.sh install.sh uninstall.sh tests/test_install.sh; do
    sh -n "${script}"
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    python3 -m plasma_taskbar_sync --version
sh tests/test_install.sh

archive=$(mktemp -t plasma-taskbar-sync.XXXXXX)
service_check_dir=
cleanup() {
    rm -f -- "${archive}"
    if [ -n "${service_check_dir}" ]; then
        rm -f -- "${service_check_dir}/plasma-taskbar-sync.service"
        rmdir -- "${service_check_dir}"
    fi
}
trap cleanup EXIT HUP INT TERM
python3 tools/build_zipapp.py src "${archive}"
chmod 755 "${archive}"
"${archive}" --version

if command -v systemd-analyze >/dev/null 2>&1; then
    service_check_dir=$(mktemp -d -t plasma-taskbar-sync-systemd.XXXXXX)
    service_check="${service_check_dir}/plasma-taskbar-sync.service"
    exec_start_count=0
    while IFS= read -r unit_line; do
        if [ "${unit_line}" = \
            "ExecStart=%h/.local/bin/plasma-taskbar-sync watch" ]; then
            printf 'ExecStart=%s watch\n' "${archive}"
            exec_start_count=$((exec_start_count + 1))
        else
            printf '%s\n' "${unit_line}"
        fi
    done < systemd/plasma-taskbar-sync.service > "${service_check}"
    if [ "${exec_start_count}" -ne 1 ]; then
        echo "Unexpected ExecStart in systemd/plasma-taskbar-sync.service" >&2
        exit 1
    fi

    verify_output=$(mktemp -t plasma-taskbar-sync-systemd.XXXXXX)
    if ! systemd-analyze verify "${service_check}" 2>"${verify_output}"; then
        filtered_output="${verify_output}.filtered"
        grep -v \
            -e '^Failed to turn off SO_PASSRIGHTS.*Operation not permitted$' \
            -e '^Failed to enable SO_PASSCRED.*Operation not permitted$' \
            "${verify_output}" > "${filtered_output}" || true
        if [ ! -s "${filtered_output}" ] && grep -q "Operation not permitted" "${verify_output}"; then
            echo "skip: systemd-analyze is blocked by this environment"
        else
            cat "${verify_output}" >&2
            rm -f -- "${verify_output}" "${filtered_output}"
            exit 1
        fi
        rm -f -- "${filtered_output}"
    fi
    rm -f -- "${verify_output}"
fi

trap - EXIT HUP INT TERM
cleanup
echo "All checks passed."
