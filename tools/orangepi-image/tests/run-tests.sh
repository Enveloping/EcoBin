#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool_root="$(cd "${script_directory}/.." && pwd)"

python3 "${tool_root}/lib/validate_inputs.py" --allow-unlocked
bash -n \
    "${tool_root}/build-image.sh" \
    "${tool_root}/rebuild-rootfs.sh" \
    "${tool_root}/sanitize-candidate.sh" \
    "${tool_root}/attest-candidates.sh" \
    "${tool_root}/seal-image.sh" \
    "${tool_root}/release-image.sh" \
    "${tool_root}/verify-image.sh" \
    "${tool_root}/expand-rootfs.sh" \
    "${tool_root}/flash-and-verify.sh" \
    "${tool_root}/trusted-flash-entry.sh" \
    "${script_directory}/test-deterministic-ext4.sh" \
    "${script_directory}/run-tests.sh"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v \
    -s "${script_directory}" -p 'test_*.py'
bash "${script_directory}/test-deterministic-ext4.sh"
printf 'ecobin-orangepi-image-tests=PASS\n'
