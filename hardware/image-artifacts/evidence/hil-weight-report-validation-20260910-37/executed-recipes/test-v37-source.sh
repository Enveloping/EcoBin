#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
workspace=/mnt/c/D/004-Project/002-Java/database-refactor-three-end-sync
repository=/var/lib/ecobin-image-factory/source-v37-20260910/repository
test_root=/var/lib/ecobin-image-factory/test-v37-20260910
previous=/var/lib/ecobin-image-factory/source-v35-20260910/repository
python=/var/lib/ecobin-image-factory/test-v35-20260910/venv/bin/python
[[ -d "$repository/.git" && -d "$test_root" && ! -e "$test_root/tests-final.log" ]]
cmp -s "$repository/hardware/uv.lock" "$previous/hardware/uv.lock"
cmp -s "$repository/hardware/pyproject.toml" "$previous/hardware/pyproject.toml"
[[ "$(stat -c %a "$test_root")" == 700 ]]
exec > >(tee "$test_root/tests-final.log") 2>&1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$workspace/hardware/image-artifacts/local:$repository/hardware"
"$python" --version
cd "$repository/hardware"
# Test fixtures intentionally create ordinary 0755 directories and 0644 files.
# Keep protected build inputs/logs private, but do not alter fixture semantics.
(umask 022; "$python" -m pytest -q -p no:cacheprovider -p v35_clock_fixture --basetemp /var/tmp/eb37-final)
printf 'v37-hardware-tests=PASS\n'
