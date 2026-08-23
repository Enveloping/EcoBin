#!/usr/bin/env bash
set -euo pipefail
umask 077

# This file belongs to the separately controlled factory-tool installation. It
# is deliberately never copied into an image release directory.
script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
release_directory=""
trust_policy="/etc/ecobin-image-factory/formal-release-policy.json"
public_key="/etc/ecobin-image-factory/release-signing-public.pem"
signing_key_id=""
device=""
confirm_device=""
verify_only=false
snapshot_root="/var/lib/ecobin-image-factory/flash-staging"
snapshot_directory=""

fail() { printf 'trusted-card-flash=FAIL: %s\n' "$1" >&2; exit 1; }

cleanup() {
    if [[ -n "${snapshot_directory}" && -d "${snapshot_directory}" ]]; then
        case "${snapshot_directory}" in
            /var/lib/ecobin-image-factory/flash-staging/.ecobin-flash.*)
                rm -rf -- "${snapshot_directory}"
                ;;
            *) return 1 ;;
        esac
    fi
}
trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage: trusted-flash-entry.sh --release-dir DIR --signing-key-id ID --verify-only
   or: trusted-flash-entry.sh --release-dir DIR --signing-key-id ID
       --device /dev/DEVICE --confirm-device /dev/DEVICE

Run only from the separately installed, root-owned factory-tool directory.
The release directory is untrusted until this entry verifies its signed exact
inventory with the public key pinned by the external formal-release policy.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --release-dir) release_directory="$2"; shift 2 ;;
        --signing-key-id) signing_key_id="$2"; shift 2 ;;
        --device) device="$2"; shift 2 ;;
        --confirm-device) confirm_device="$2"; shift 2 ;;
        --verify-only) verify_only=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ "$(id -u)" = 0 ]] || fail "trusted flashing requires root"
ulimit -c 0 || fail "core dumps must be disabled before handling the sealed image"
[[ -n "${release_directory}${signing_key_id}" ]] \
    || fail "release directory and signing key ID are required"
if [[ "${verify_only}" = true ]]; then
    [[ -z "${device}${confirm_device}" ]] \
        || fail "--verify-only cannot be combined with a device"
else
    [[ -n "${device}" && "${device}" = "${confirm_device}" ]] \
        || fail "device confirmation must exactly match"
fi
for command_name in python3 sha256sum stat readlink bash swapon; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required trusted-tool command is missing: ${command_name}"
done
[[ -z "$(swapon --noheadings --show=NAME 2>/dev/null)" ]] \
    || fail "active swap is forbidden while handling the sealed image"
[[ -d "${release_directory}" && ! -L "${release_directory}" ]] \
    || fail "release directory is unavailable"
release_directory="$(readlink -f -- "${release_directory}")"
trust_policy="$(readlink -f -- "${trust_policy}")"
public_key="$(readlink -f -- "${public_key}")"
python3 "${script_directory}/lib/release_trust.py" validate-policy \
    --trust-policy "${trust_policy}" --repository-root "${repository_root}" >/dev/null
[[ -d "${snapshot_root}" && ! -L "${snapshot_root}" \
    && "$(stat -c '%u:%g:%a' -- "${snapshot_root}")" = 0:0:700 ]] \
    || fail "trusted snapshot root must be pre-created as root:root mode 0700"
python3 "${script_directory}/lib/release_trust.py" validate-directory \
    --directory "${snapshot_root}" >/dev/null
snapshot_directory="$(mktemp -d "${snapshot_root}/.ecobin-flash.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "${snapshot_directory}")" = 0:0:700 ]] \
    || fail "trusted snapshot directory permissions are unsafe"

release_checksums="${release_directory}/release-checksums.txt"
release_signature="${release_directory}/release-checksums.sig"
for input in "${release_checksums}" "${release_signature}" "${public_key}"; do
    [[ -f "${input}" && ! -L "${input}" && "$(stat -c '%h' -- "${input}")" = 1 ]] \
        || fail "trusted verification input is unsafe"
done
checksums_file="${snapshot_directory}/release-checksums.txt"
signature_file="${snapshot_directory}/release-checksums.sig"
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${release_checksums}" --destination "${checksums_file}" \
    --mode 0644 --maximum-bytes 1048576 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${release_signature}" --destination "${signature_file}" \
    --mode 0644 --maximum-bytes 4096 >/dev/null

# This is the first operation that consumes release-controlled content. No
# script, manifest or schema from the release has run before this succeeds.
python3 "${script_directory}/lib/release_trust.py" verify-signature \
    --trust-policy "${trust_policy}" --role releaseSigning \
    --public-key "${public_key}" --key-id "${signing_key_id}" \
    --payload "${checksums_file}" --signature "${signature_file}" >/dev/null

mapfile -t checksum_lines < "${checksums_file}"
[[ ${#checksum_lines[@]} = 19 ]] \
    || fail "signed release inventory entry count is invalid"
archive_name="${checksum_lines[0]#*  }"
expected_names=(
    "${archive_name}"
    image-manifest.json
    sbom.spdx.json
    apt-packages.txt
    python-packages.txt
    build-attestation.json
    build-attestation.sig
    rootfs-qualification-evidence.json
    target-media-qualification-evidence.json
    seal-evidence.json
    seal-evidence.sig
    schemas/release-manifest.schema.json
    schemas/build-attestation.schema.json
    schemas/rootfs-qualification-evidence.schema.json
    schemas/target-media-qualification-evidence.schema.json
    schemas/seal-evidence.schema.json
    flash-and-verify.sh
    flash-and-verify.ps1
    factory-checklist.md
)
# Keep the count tied to the exact trusted array rather than accepting
# arbitrary additional signed programs.
[[ ${#checksum_lines[@]} = ${#expected_names[@]} ]] \
    || fail "signed release inventory does not match trusted policy"
for index in "${!expected_names[@]}"; do
    line="${checksum_lines[index]}"
    [[ "${line}" =~ ^([0-9a-f]{64})\ \ ([A-Za-z0-9._/-]+)$ ]] \
        || fail "signed inventory line is malformed"
    digest="${BASH_REMATCH[1]}"
    relative="${BASH_REMATCH[2]}"
    [[ "${relative}" = "${expected_names[index]}" \
        && "${relative}" != /* && "${relative}" != ../* \
        && "${relative}" != *../* ]] \
        || fail "signed inventory name or order differs from trusted policy"
    source_member="${release_directory}/${relative}"
    snapshot_member="${snapshot_directory}/${relative}"
    snapshot_parent="$(dirname -- "${snapshot_member}")"
    if [[ ! -d "${snapshot_parent}" ]]; then
        mkdir -m 0700 -- "${snapshot_parent}"
    fi
    snapshot_mode=0644
    [[ "${relative}" = flash-and-verify.sh ]] && snapshot_mode=0755
    python3 "${script_directory}/lib/release_trust.py" snapshot \
        --source "${source_member}" --destination "${snapshot_member}" \
        --mode "${snapshot_mode}" --expected-sha256 "${digest}" >/dev/null
done

archive="${snapshot_directory}/${archive_name}"
manifest="${snapshot_directory}/image-manifest.json"
signed_flash="${snapshot_directory}/flash-and-verify.sh"
arguments=(
    --image-zst "${archive}"
    --checksums-file "${checksums_file}"
    --signature-file "${signature_file}"
    --public-key-file "${public_key}"
    --manifest "${manifest}"
    --signing-key-id "${signing_key_id}"
)
if [[ "${verify_only}" = true ]]; then
    arguments+=(--verify-only)
else
    arguments+=(--device "${device}" --confirm-device "${confirm_device}")
fi
bash "${signed_flash}" "${arguments[@]}"
