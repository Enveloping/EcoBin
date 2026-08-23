#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
config_directory="${script_directory}"
trust_policy=""
candidate_a=""
manifest_a=""
candidate_b=""
manifest_b=""
software_payload=""
software_payload_sha256=""
release_id=""
version=""
git_commit=""
private_key=""
public_key=""
key_id=""
output_attestation=""
output_signature=""
rootfs_evidence=""
target_media_evidence=""
receipt_a=""; receipt_signature_a=""; receipt_public_key_a=""
receipt_b=""; receipt_signature_b=""; receipt_public_key_b=""
staging_directory=""

fail() { printf 'candidate-attestation=FAIL: %s\n' "$1" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: attest-candidates.sh --candidate-a FILE --manifest-a FILE
       --candidate-b FILE --manifest-b FILE --software-payload DIR
       --software-payload-sha256 HEX --release-id ID --version X.Y.Z
       --git-commit HEX --trust-policy FILE --signing-private-key FILE
       --signing-public-key FILE --signing-key-id ID
       --output-attestation FILE --output-signature FILE [--config-dir DIR]
       --rootfs-qualification-evidence FILE
       --target-media-qualification-evidence FILE
       --receipt-a FILE --receipt-signature-a FILE --receipt-public-key-a FILE
       --receipt-b FILE --receipt-signature-b FILE --receipt-public-key-b FILE

The controlled attestation station audits two independent no-secret candidates,
requires byte-identical raw images and manifests, and signs one canonical proof.
EOF
}

cleanup() {
    if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
        case "$(basename -- "${staging_directory}")" in
            .ecobin-build-attestation.*) rm -rf -- "${staging_directory}" ;;
            *) return 1 ;;
        esac
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir) config_directory="$2"; shift 2 ;;
        --trust-policy) trust_policy="$2"; shift 2 ;;
        --candidate-a) candidate_a="$2"; shift 2 ;;
        --manifest-a) manifest_a="$2"; shift 2 ;;
        --candidate-b) candidate_b="$2"; shift 2 ;;
        --manifest-b) manifest_b="$2"; shift 2 ;;
        --software-payload) software_payload="$2"; shift 2 ;;
        --software-payload-sha256) software_payload_sha256="$2"; shift 2 ;;
        --release-id) release_id="$2"; shift 2 ;;
        --version) version="$2"; shift 2 ;;
        --git-commit) git_commit="$2"; shift 2 ;;
        --signing-private-key) private_key="$2"; shift 2 ;;
        --signing-public-key) public_key="$2"; shift 2 ;;
        --signing-key-id) key_id="$2"; shift 2 ;;
        --output-attestation) output_attestation="$2"; shift 2 ;;
        --output-signature) output_signature="$2"; shift 2 ;;
        --rootfs-qualification-evidence) rootfs_evidence="$2"; shift 2 ;;
        --target-media-qualification-evidence) target_media_evidence="$2"; shift 2 ;;
        --receipt-a) receipt_a="$2"; shift 2 ;;
        --receipt-signature-a) receipt_signature_a="$2"; shift 2 ;;
        --receipt-public-key-a) receipt_public_key_a="$2"; shift 2 ;;
        --receipt-b) receipt_b="$2"; shift 2 ;;
        --receipt-signature-b) receipt_signature_b="$2"; shift 2 ;;
        --receipt-public-key-b) receipt_public_key_b="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ -n "${candidate_a}${manifest_a}${candidate_b}${manifest_b}" \
    && -n "${software_payload}${software_payload_sha256}${release_id}${version}" \
    && -n "${git_commit}${trust_policy}${private_key}${public_key}${key_id}" \
    && -n "${output_attestation}${output_signature}${rootfs_evidence}${target_media_evidence}" \
    && -n "${receipt_a}${receipt_signature_a}${receipt_public_key_a}${receipt_b}${receipt_signature_b}${receipt_public_key_b}" ]] \
    || fail "all attestation inputs are required"
[[ "$(id -u)" = 0 ]] || fail "candidate attestation requires root image audit"
ulimit -c 0 || fail "core dumps must be disabled before handling signing keys"
for command_name in python3 openssl stat readlink cmp sha256sum git swapon; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done
[[ -z "$(swapon --noheadings --show=NAME 2>/dev/null)" ]] \
    || fail "active swap is forbidden on the build-attestation station"
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked \
    --target-media-qualification-evidence "${target_media_evidence}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" validate-policy \
    --trust-policy "${trust_policy}" --repository-root "${repository_root}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" check-key \
    --trust-policy "${trust_policy}" --role buildAttestation \
    --key "${public_key}" --key-id "${key_id}" >/dev/null

for input in "${private_key}" "${public_key}" "${trust_policy}" "${rootfs_evidence}" "${target_media_evidence}" "${receipt_a}" "${receipt_signature_a}" "${receipt_public_key_a}" "${receipt_b}" "${receipt_signature_b}" "${receipt_public_key_b}"; do
    [[ -f "${input}" && ! -L "${input}" && "$(stat -c '%h' -- "${input}")" = 1 ]] \
        || fail "attestation input must be a regular non-linked file"
done
[[ -d "${software_payload}" && ! -L "${software_payload}" ]] \
    || fail "controlled software payload is unavailable"
config_directory="$(readlink -f -- "${config_directory}")"
trust_policy="$(readlink -f -- "${trust_policy}")"
software_payload="$(readlink -f -- "${software_payload}")"
private_key="$(readlink -f -- "${private_key}")"
public_key="$(readlink -f -- "${public_key}")"
[[ ! "${candidate_a}" -ef "${candidate_b}" \
    && ! "${manifest_a}" -ef "${manifest_b}" ]] \
    || fail "independent candidate artifacts must be distinct files"

output_parent="$(dirname -- "${output_attestation}")"
signature_parent="$(dirname -- "${output_signature}")"
[[ -d "${output_parent}" && -d "${signature_parent}" ]] \
    || fail "controlled output directory must already exist"
output_parent="$(readlink -f -- "${output_parent}")"
signature_parent="$(readlink -f -- "${signature_parent}")"
[[ "${output_parent}" = "${signature_parent}" ]] \
    || fail "attestation and signature outputs must share one controlled directory"
[[ "$(stat -c '%u:%g:%a' -- "${output_parent}")" = 0:0:700 ]] \
    || fail "attestation output directory must be root:root mode 0700"
python3 "${script_directory}/lib/release_trust.py" validate-directory \
    --directory "${output_parent}" >/dev/null
output_attestation="${output_parent}/$(basename -- "${output_attestation}")"
output_signature="${output_parent}/$(basename -- "${output_signature}")"
[[ ! -e "${output_attestation}" && ! -L "${output_attestation}" \
    && ! -e "${output_signature}" && ! -L "${output_signature}" \
    && "${output_attestation}" != "${output_signature}" ]] \
    || fail "attestation outputs already exist or overlap"
staging_directory="$(mktemp -d \
    "${output_parent}/.ecobin-build-attestation.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "${staging_directory}")" = 0:0:700 ]] \
    || fail "attestation staging directory permissions are unsafe"
snapshot_candidate_a="${staging_directory}/candidate-a.img"
snapshot_manifest_a="${staging_directory}/candidate-a-manifest.json"
snapshot_candidate_b="${staging_directory}/candidate-b.img"
snapshot_manifest_b="${staging_directory}/candidate-b-manifest.json"
snapshot_payload_lock="${staging_directory}/software-payload.lock.json"
snapshot_rootfs_evidence="${staging_directory}/rootfs-qualification-evidence.json"
snapshot_target_media_evidence="${staging_directory}/target-media-qualification-evidence.json"
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${candidate_a}" --destination "${snapshot_candidate_a}" \
    --mode 0600 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${manifest_a}" --destination "${snapshot_manifest_a}" \
    --mode 0600 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${candidate_b}" --destination "${snapshot_candidate_b}" \
    --mode 0600 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${manifest_b}" --destination "${snapshot_manifest_b}" \
    --mode 0600 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${software_payload}/software-payload.lock.json" \
    --destination "${snapshot_payload_lock}" --mode 0600 \
    --expected-sha256 "${software_payload_sha256}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot --source "${rootfs_evidence}" --destination "${snapshot_rootfs_evidence}" --mode 0600 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot --source "${target_media_evidence}" --destination "${snapshot_target_media_evidence}" --mode 0600 --maximum-bytes 1048576 >/dev/null
export ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE="${snapshot_target_media_evidence}"
for suffix in a b; do
    if [[ "${suffix}" = a ]]; then
        source_receipt="${receipt_a}"; source_signature="${receipt_signature_a}"; source_public="${receipt_public_key_a}"
    else
        source_receipt="${receipt_b}"; source_signature="${receipt_signature_b}"; source_public="${receipt_public_key_b}"
    fi
    python3 "${script_directory}/lib/release_trust.py" snapshot --source "${source_receipt}" --destination "${staging_directory}/receipt-${suffix}.json" --mode 0600 >/dev/null
    python3 "${script_directory}/lib/release_trust.py" snapshot --source "${source_signature}" --destination "${staging_directory}/receipt-${suffix}.sig" --mode 0600 >/dev/null
    python3 "${script_directory}/lib/release_trust.py" snapshot --source "${source_public}" --destination "${staging_directory}/receipt-${suffix}.pem" --mode 0600 >/dev/null
done
candidate_a="${snapshot_candidate_a}"
manifest_a="${snapshot_manifest_a}"
candidate_b="${snapshot_candidate_b}"
manifest_b="${snapshot_manifest_b}"
cmp -s -- "${candidate_a}" "${candidate_b}" \
    || fail "independent candidate raw images differ byte-for-byte"
cmp -s -- "${manifest_a}" "${manifest_b}" \
    || fail "independent candidate manifests differ byte-for-byte"
for candidate in "${candidate_a}" "${candidate_b}"; do
    bash "${script_directory}/verify-image.sh" --candidate \
        --config-dir "${config_directory}" --image "${candidate}" \
        --software-payload-sha256 "${software_payload_sha256}" \
        --release-id "${release_id}" --version "${version}" \
        --git-commit "${git_commit}"
done
temporary_attestation="${staging_directory}/build-attestation.json"
temporary_signature="${staging_directory}/build-attestation.sig"
python3 "${script_directory}/lib/generate_build_attestation.py" \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --repository-root "${repository_root}" \
    --candidate-a "${candidate_a}" --manifest-a "${manifest_a}" \
    --candidate-b "${candidate_b}" --manifest-b "${manifest_b}" \
    --software-payload-lock "${snapshot_payload_lock}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --rootfs-qualification-evidence "${snapshot_rootfs_evidence}" \
    --target-media-qualification-evidence "${snapshot_target_media_evidence}" \
    --receipt-a "${staging_directory}/receipt-a.json" --receipt-signature-a "${staging_directory}/receipt-a.sig" --receipt-public-key-a "${staging_directory}/receipt-a.pem" \
    --receipt-b "${staging_directory}/receipt-b.json" --receipt-signature-b "${staging_directory}/receipt-b.sig" --receipt-public-key-b "${staging_directory}/receipt-b.pem" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" --output "${temporary_attestation}"
python3 "${script_directory}/lib/release_trust.py" sign \
    --trust-policy "${trust_policy}" --role buildAttestation \
    --private-key "${private_key}" --key-id "${key_id}" \
    --payload "${temporary_attestation}" --output "${temporary_signature}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" verify-build \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --attestation "${temporary_attestation}" \
    --signature "${temporary_signature}" --public-key "${public_key}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --candidate "${candidate_a}" --candidate-manifest "${manifest_a}" >/dev/null
chmod 0644 -- "${temporary_attestation}" "${temporary_signature}"
python3 "${script_directory}/lib/release_trust.py" publish \
    --source "${temporary_attestation}" --destination "${output_attestation}" \
    --mode 0644 >/dev/null
python3 "${script_directory}/lib/release_trust.py" publish \
    --source "${temporary_signature}" --destination "${output_signature}" \
    --mode 0644 >/dev/null
rm -f -- "${temporary_attestation}" "${temporary_signature}" \
    "${snapshot_candidate_a}" "${snapshot_manifest_a}" \
    "${snapshot_candidate_b}" "${snapshot_manifest_b}" \
    "${snapshot_payload_lock}" "${snapshot_rootfs_evidence}" \
    "${snapshot_target_media_evidence}" \
    "${staging_directory}/receipt-a.json" "${staging_directory}/receipt-a.sig" "${staging_directory}/receipt-a.pem" \
    "${staging_directory}/receipt-b.json" "${staging_directory}/receipt-b.sig" "${staging_directory}/receipt-b.pem"
rmdir -- "${staging_directory}"
staging_directory=""
printf 'candidate-attestation=PASS attestation=%s signature=%s\n' \
    "${output_attestation}" "${output_signature}"
