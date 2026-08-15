#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote_support_root="$(cd "${script_directory}/.." && pwd)"

for script in "${remote_support_root}"/bin/*.sh "${script_directory}"/*.sh; do
    bash -n "${script}"
done
python3 - "${remote_support_root}" "${script_directory}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
tests = pathlib.Path(sys.argv[2])
for source in (
    root / "lib" / "ecobin_remote_support.py",
    root / "bin" / "ecobin-authorized-keys",
    root / "bin" / "ecobin-lease-guard",
    root / "bin" / "ecobin-leasectl",
    tests / "test_remote_support.py",
):
    compile(source.read_text(encoding="utf-8"), str(source), "exec")
PY

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
    "${script_directory}/test_remote_support.py"
bash "${script_directory}/test-ca-helpers.sh"
printf 'ecobin-remote-support-tests=PASS\n'
