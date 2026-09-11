#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
workspace=/mnt/c/D/004-Project/002-Java/database-refactor-three-end-sync
image=/var/lib/ecobin-image-factory/hil-v36-20260910-snapshot/candidate-single-card-hil-20260910-36/ecobin-orangepi-zero3-0.1.0-single-card.20260910.36.img
container_image=docker.io/library/debian:bookworm-20260803-slim@sha256:817e6cf99d6fc127ff4ffe8580049b60deba0adfbbb2bd65ddc3ef8fbb7aade0
loop=
root=
state=
cleanup() {
    status=$?
    set +e
    if [[ -n "$loop" ]]; then
        for _attempt in {1..50}; do
            while IFS= read -r target; do
                [[ -n "$target" ]] || continue
                case "$target" in
                    "$root"|/mnt/wsl/docker-desktop-bind-mounts/Ubuntu-24.04/*) umount -- "$target" ;;
                    *) printf 'unexpected-loop-mount=%s\n' "$target" >&2; status=1 ;;
                esac
            done < <(findmnt -rn -S "$loop" -o TARGET 2>/dev/null || true)
            losetup -d "$loop" 2>/dev/null || true
            if ! losetup "$loop" >/dev/null 2>&1; then loop=; break; fi
            sleep 0.1
        done
        [[ -z "$loop" ]] || status=1
    fi
    [[ -z "$root" ]] || rmdir "$root" 2>/dev/null || true
    # Keep synthetic database evidence in the private build directory.
    return "$status"
}
trap cleanup EXIT
[[ "$(id -u)" = 0 && -f "$image" && ! -L "$image" ]]
test_root=/var/lib/ecobin-image-factory/test-v36-20260910
state="$test_root/arm64-state"
mkdir -m 0700 "$state"
root="$(mktemp -d /var/lib/ecobin-image-factory/v36-smoke-root.XXXXXXXX)"
loop="$(losetup --find --show --read-only --offset 4194304 --sizelimit 2566914048 -- "$image")"
mount -t ext4 -o ro,noload,nodev,nosuid "$loop" "$root"
run_in_image() {
    docker run --rm -i --platform linux/arm64 --network none --read-only \
        --security-opt no-new-privileges --cap-drop ALL --cap-add SYS_CHROOT \
        --mount "type=bind,src=$root,dst=/image,readonly" \
        --mount "type=bind,src=$state,dst=/image/tmp" \
        --entrypoint /usr/sbin/chroot "$container_image" /image "$@"
}
run_in_image /usr/bin/python3.11 --version
run_in_image /opt/ecobin/updater/current/.venv/bin/python - \
    < "$workspace/hardware/image-artifacts/local/v35-installed-quarantine-smoke.py"
run_in_image /usr/bin/python3 -c \
    'import sys; sys.path.insert(0,"/opt/ecobin/communication/current/app"); import communication_agent, communication_router, onenet_wire; print("installed-communication-import=PASS")'
run_in_image /opt/ecobin/hardware/current/.venv/bin/python -c \
    'import sys; sys.path.insert(0,"/opt/ecobin/hardware/current/app"); import main, work_manager, job_safety, edge_store; assert hasattr(work_manager.WorkManager,"quarantine_delivery_recovery"); assert hasattr(edge_store.EdgeStore,"quarantine_delivery_recovery"); print("installed-business-quarantine-import=PASS")'
run_in_image /opt/ecobin/factory-test/current/.venv/bin/python - \
    < "$workspace/hardware/image-artifacts/local/v36-installed-weight-smoke.py"
printf 'v36-arm64-smoke=PASS imageReadOnly=true networkIsolated=true syntheticStateOnly=true\n'
