#!/usr/bin/env bash
set -euo pipefail
umask 077

# Runs only inside the digest-pinned disposable Debian builder.  The outer
# launcher mounts the repository and source artifact read-only.
script_directory=/workspace/tools/orangepi-image
build_mode=image
case "${1:-}" in
    --software-payload-only)
        build_mode=software-payload
        shift
        ;;
    --runtime-release-only)
        build_mode=runtime-release
        shift
        ;;
    --business-release-only)
        build_mode=business-release
        shift
        ;;
esac
builder_lock="${script_directory}/builder.lock"
# The locked debian:bookworm-slim base deliberately has no CA bundle.  For the
# first HTTPS transfer only, disable TLS peer verification while APT still
# authenticates the immutable snapshot's InRelease and package hashes through
# the pinned Debian archive keyring.  Install the exact CA package, require its
# bundle, then repeat update with normal HTTPS verification before installing
# any remaining builder tool.  This avoids both a CA bootstrap cycle and an
# unauthenticated package source.
snapshot_url="https://snapshot.debian.org/archive/debian/20260803T000000Z/"
snapshot_sources="/tmp/ecobin-builder-snapshot.sources"

fail() {
    printf 'builder-bootstrap=FAIL: %s\n' "$1" >&2
    exit 1
}

[[ "$(id -u)" = 0 ]] || fail "builder bootstrap requires container root"
[[ -f "${builder_lock}" && ! -L "${builder_lock}" ]] \
    || fail "builder lock is unavailable"
command -v apt-get >/dev/null 2>&1 || fail "builder image has no apt-get"

cat > "${snapshot_sources}" <<EOF
Types: deb
URIs: ${snapshot_url}
Suites: bookworm
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF

apt_options=(
    -o "Acquire::Check-Valid-Until=false"
    -o "Acquire::Retries=4"
    -o "Acquire::http::Timeout=60"
    -o "Acquire::https::Timeout=60"
    -o "Dir::Etc::sourcelist=${snapshot_sources}"
    -o "Dir::Etc::sourceparts=-"
    -o "APT::Get::List-Cleanup=0"
)
bootstrap_apt_options=(
    "${apt_options[@]}"
    -o "Acquire::https::Verify-Peer=false"
)
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
export TZ=UTC
export PYTHONDONTWRITEBYTECODE=1

apt-get "${bootstrap_apt_options[@]}" update
# Python is the only bootstrap parser.  Both bootstrap packages are installed
# at their exact locked versions and rechecked with every other tool later.
apt-get "${bootstrap_apt_options[@]}" install -y --no-install-recommends \
    ca-certificates=20230311+deb12u1 \
    python3=3.11.2-1+b1
[[ -s /etc/ssl/certs/ca-certificates.crt \
    && ! -L /etc/ssl/certs/ca-certificates.crt ]] \
    || fail "bootstrap CA bundle is unavailable"
apt-get "${apt_options[@]}" update

mapfile -t locked_packages < <(
    python3 - "${builder_lock}" "${build_mode}" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if document.get("lockState") != "LOCKED":
    raise SystemExit("builder lock is not locked")
mapping = {
    "bash": "bash",
    "caCertificates": "ca-certificates",
    "coreutils": "coreutils",
    "debianArchiveKeyring": "debian-archive-keyring",
    "e2fsprogs": "e2fsprogs",
    "fdisk": "fdisk",
    "findutils": "findutils",
    "git": "git",
    "grep": "grep",
    "gzip": "gzip",
    "jq": "jq",
    "mawk": "mawk",
    "mount": "mount",
    "openssl": "openssl",
    "p7zip": "p7zip-full",
    "python": "python3",
    "python311Venv": "python3.11-venv",
    "pythonCryptography": "python3-cryptography",
    "pythonPip": "python3-pip",
    "pythonVenv": "python3-venv",
    "qemuUserStatic": "qemu-user-static",
    "sed": "sed",
    "utilLinux": "util-linux",
    "xz": "xz-utils",
    "zstd": "zstd",
}
build_mode = sys.argv[2]
if build_mode not in {
    "image",
    "runtime-release",
    "business-release",
    "software-payload",
}:
    raise SystemExit("builder mode is invalid")
image_only_tools = {
    "e2fsprogs",
    "fdisk",
    "jq",
    "mount",
    "p7zip",
    "qemuUserStatic",
    "utilLinux",
    "xz",
    "zstd",
}
tools = document.get("tools")
if not isinstance(tools, dict) or set(tools) != set(mapping) | {"uv"}:
    raise SystemExit("builder tool mapping differs from the lock")
for key, package in mapping.items():
    if build_mode != "image" and key in image_only_tools:
        continue
    value = tools[key]
    if not isinstance(value, str) or not value:
        raise SystemExit("builder tool version is absent")
    print(f"{package}={value}")
PY
)
[[ "${#locked_packages[@]}" -gt 0 ]] \
    || fail "builder package lock produced no packages"

apt-get "${apt_options[@]}" install -y --no-install-recommends \
    "${locked_packages[@]}"

if [[ "${build_mode}" = image ]]; then
    mapfile -t target_packages < <(
        python3 - "${script_directory}/apt-packages.lock" <<'PY'
import pathlib
import sys
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#"):
        print(line.split(" sha256=", 1)[0])
PY
    )
    [[ "${#target_packages[@]}" -gt 0 ]] \
        || fail "target package lock produced no packages"
    target_deb_directory="/tmp/ecobin-target-debs"
    mkdir -m 0755 -- "${target_deb_directory}"
    (
        cd "${target_deb_directory}"
        apt-get "${apt_options[@]}" download "${target_packages[@]}"
    )
    mapfile -t downloaded_debs < <(find "${target_deb_directory}" \
        -mindepth 1 -maxdepth 1 -type f -name '*.deb' -printf '%p\n' | sort)
    [[ "${#downloaded_debs[@]}" = "${#target_packages[@]}" ]] \
        || fail "downloaded target deb set is incomplete"
    chmod 0644 -- "${downloaded_debs[@]}"
    export ECOBIN_TARGET_DEB_DIRECTORY="${target_deb_directory}"
fi

python3 - "${builder_lock}" "${build_mode}" <<'PY'
import json
import pathlib
import subprocess
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
mapping = {
    "bash": "bash",
    "caCertificates": "ca-certificates",
    "coreutils": "coreutils",
    "debianArchiveKeyring": "debian-archive-keyring",
    "e2fsprogs": "e2fsprogs",
    "fdisk": "fdisk",
    "findutils": "findutils",
    "git": "git",
    "grep": "grep",
    "gzip": "gzip",
    "jq": "jq",
    "mawk": "mawk",
    "mount": "mount",
    "openssl": "openssl",
    "p7zip": "p7zip-full",
    "python": "python3",
    "python311Venv": "python3.11-venv",
    "pythonCryptography": "python3-cryptography",
    "pythonPip": "python3-pip",
    "pythonVenv": "python3-venv",
    "qemuUserStatic": "qemu-user-static",
    "sed": "sed",
    "utilLinux": "util-linux",
    "xz": "xz-utils",
    "zstd": "zstd",
}
build_mode = sys.argv[2]
if build_mode not in {
    "image",
    "runtime-release",
    "business-release",
    "software-payload",
}:
    raise SystemExit("builder mode is invalid")
image_only_tools = {
    "e2fsprogs",
    "fdisk",
    "jq",
    "mount",
    "p7zip",
    "qemuUserStatic",
    "utilLinux",
    "xz",
    "zstd",
}
for key, package in mapping.items():
    if build_mode != "image" and key in image_only_tools:
        continue
    actual = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", package],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout
    if actual != document["tools"][key]:
        raise SystemExit(f"installed builder package differs: {package}")
PY

python3 - "${builder_lock}" <<'PY'
import hashlib
import io
import json
import os
import pathlib
import stat
import sys
import tarfile
import urllib.request

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
artifact = document["uvArtifact"]
expected_size = artifact["downloadBytes"]
request = urllib.request.Request(
    artifact["url"],
    headers={"User-Agent": "EcoBin-reproducible-image-builder"},
)
try:
    with pathlib.Path("/locked-uv.tar.gz").open("rb") as response:
        payload = response.read(expected_size + 1)
except OSError:
    raise SystemExit("locked uv artifact could not be downloaded") from None
if len(payload) != expected_size:
    raise SystemExit("locked uv artifact byte length differs")
if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
    raise SystemExit("locked uv artifact digest differs")

prefix = "uv-aarch64-unknown-linux-gnu/"
expected_files = {prefix + "uv", prefix + "uvx"}
expected = {prefix.rstrip("/"), *expected_files}
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
    members = archive.getmembers()
    names = {member.name for member in members}
    invalid = any(
        member.issym()
        or member.islnk()
        or (
            member.name in expected_files
            and (not member.isfile() or member.size <= 0 or member.size > 100 * 1024 * 1024)
        )
        or (member.name == prefix.rstrip("/") and not member.isdir())
        for member in members
    )
    if names != expected or invalid:
        raise SystemExit("locked uv artifact members differ")
    for member in members:
        if member.name not in expected_files:
            continue
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("locked uv artifact member is unreadable")
        destination = pathlib.Path("/usr/local/bin") / pathlib.PurePosixPath(member.name).name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(destination, flags, 0o755)
        except FileExistsError:
            raise SystemExit("builder uv destination already exists") from None
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(destination, 0o755)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
PY

expected_uv_version="$(
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tools"]["uv"])' \
        "${builder_lock}"
)"
case "$(uv --version)" in
    "uv ${expected_uv_version}"|"uv ${expected_uv_version} "*) ;;
    *) fail "installed uv version differs from the lock" ;;
esac

apt-get clean
rm -rf -- /var/lib/apt/lists/*
if [[ "${build_mode}" = runtime-release ]]; then
    exec python3 /workspace/hardware/install/build_runtime_release.py "$@"
fi
if [[ "${build_mode}" = business-release ]]; then
    exec python3 /workspace/hardware/install/build_business_release.py "$@"
fi
if [[ "${build_mode}" = software-payload ]]; then
    exec bash "${script_directory}/build-software-payload.sh" "$@"
fi
exec bash "${script_directory}/build-image.sh" "$@"
