#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: generate-maintenance-ca.sh --output-directory ABSOLUTE_PATH \
    --acknowledge-unencrypted-private-key

Creates an Ed25519 OpenSSH user CA outside the repository. The private key is
unencrypted so an automated signer can use it; protect it with a root-owned
secret mount, a dedicated signer, or replace it with an HSM-backed signer.
EOF
}

output_directory=
acknowledged=false
while (($# > 0)); do
    case "$1" in
        --output-directory)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            output_directory="$2"
            shift 2
            ;;
        --acknowledge-unencrypted-private-key)
            acknowledged=true
            shift
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

[[ -n "${output_directory}" && "${output_directory}" = /* ]] || {
    echo "output directory must be an absolute path" >&2
    exit 2
}
[[ "${acknowledged}" = true ]] || {
    echo "explicit acknowledgement of the unencrypted private key is required" >&2
    exit 2
}
command -v ssh-keygen >/dev/null || {
    echo "ssh-keygen is required" >&2
    exit 1
}

umask 077
existing_parent="${output_directory}"
while [[ ! -d "${existing_parent}" ]]; do
    next_parent="$(dirname "${existing_parent}")"
    [[ "${next_parent}" != "${existing_parent}" ]] || break
    existing_parent="${next_parent}"
done
repository_probe="$(cd "${existing_parent}" && pwd -P)"
while true; do
    if [[ -e "${repository_probe}/.git" ]]; then
        echo "refusing to create a CA anywhere inside a Git worktree" >&2
        exit 1
    fi
    next_probe="$(dirname "${repository_probe}")"
    [[ "${next_probe}" != "${repository_probe}" ]] || break
    repository_probe="${next_probe}"
done
if [[ -e "${output_directory}" ]]; then
    [[ -d "${output_directory}" && ! -L "${output_directory}" ]] || {
        echo "output path must be a real directory" >&2
        exit 1
    }
    [[ "$(stat -c '%u:%a' "${output_directory}")" = "$(id -u):700" ]] || {
        echo "existing output directory must be owned by the caller with mode 0700" >&2
        exit 1
    }
else
    install -d -m 0700 -- "${output_directory}"
fi
private_key="${output_directory}/maintenance-user-ca"
public_key="${private_key}.pub"
[[ ! -e "${private_key}" && ! -L "${private_key}" && \
   ! -e "${public_key}" && ! -L "${public_key}" ]] || {
    echo "refusing to overwrite an existing maintenance CA" >&2
    exit 1
}

ssh-keygen -q -t ed25519 -N '' \
    -C 'EcoBin maintenance user CA' -f "${private_key}"
chmod 0600 -- "${private_key}"
chmod 0644 -- "${public_key}"

fingerprint="$(ssh-keygen -l -E sha256 -f "${public_key}" | awk '{print $2}')"
printf 'maintenance-ca-created=YES\npublic-key=%s\nfingerprint=%s\n' \
    "${public_key}" "${fingerprint}"
