#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
workspace=/mnt/c/D/004-Project/002-Java/database-refactor-three-end-sync
controlled=/var/lib/ecobin-image-factory/hil-v37-20260910-snapshot
repository="$controlled/repository"
commit=c097673c23527a63e5c442a77cb19322e6774d42
payload="$controlled/software-payload-20260910-37"
payload_sha=a41e9ad6e2668445cc9fa9a7d48312c8caee7eca875075870f2cd225492d967d
runtime="$controlled/runtime/hardware-runtime-20260910-37/ecobin-hardware-hardware-runtime-20260910-37.tar.gz"
runtime_sha=a655ea4aeb8fc2995f3f955bda6591a30f1169f0fc8945d913b22b73928eb5b9
candidate="$controlled/candidate-single-card-hil-20260910-37"
old_log="$controlled/build-v37-candidate.power-loss.log"
python=/var/lib/ecobin-image-factory/test-v35-20260910/venv/bin/python
[[ "$(git -C "$repository" rev-parse HEAD)" = "$commit" ]]
[[ -z "$(git -C "$repository" status --porcelain --untracked-files=all)" ]]
[[ ! -e "$candidate" && ! -e "$old_log" ]]
[[ -z "$(docker ps -q)" ]]
[[ "$(sha256sum "$runtime" | awk '{print $1}')" = "$runtime_sha" ]]
[[ "$(sha256sum "$workspace/hardware/image-artifacts/local/source-bundles/repository-v37-20260910.bundle" | awk '{print $1}')" = 17af3c06b4ef5bdad762b01c34c2c945193f5ac23c1760656d35fe331ac79ae8 ]]
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repository/hardware"
"$python" -m system.image_software_installer validate-payload \
    --payload "$payload" --payload-sha256 "$payload_sha" --git-commit "$commit"
"$python" -c 'from install.runtime_release import verified_archive_stream; import sys; c=verified_archive_stream(sys.argv[1], expected_sha256=sys.argv[2], signature_path=sys.argv[1]+".sig", signing_key_id="hil_factory_20260830", trusted_public_keys_directory=sys.argv[3]); c.__enter__(); c.__exit__(None,None,None); print("v37-recovered-runtime-signature=PASS")' "$runtime" "$runtime_sha" "$controlled/runtime-trust"
[[ "$(blkid -s TYPE -o value /dev/sdc)" = swap ]]
[[ "$(awk 'NR > 1 {print $1}' /proc/swaps)" = /dev/sdc ]]
swapoff /dev/sdc
[[ -z "$(swapon --show --noheadings)" ]]
mv -- "$controlled/build-v37-candidate.log" "$old_log"
exec > >(tee "$controlled/build-v37-candidate.log") 2>&1
printf 'v37-image-resume=START completedRuntimeAndPayloadReused=true incompleteRootfsReused=false\n'
sha256sum "$old_log" "$runtime" "$payload/software-payload.lock.json"
export PATH="$workspace/hardware/image-artifacts/local/v37-tools:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
"$repository/tools/orangepi-image/run-builder.sh" \
    --source "$controlled/input/Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31.7z" \
    --output-dir "$candidate" \
    --release-id single-card-hil-20260910-37 \
    --version 0.1.0-single-card.20260910.37 \
    --software-payload "$payload" --software-payload-sha256 "$payload_sha" \
    --target-media-qualification-evidence "$controlled/input/target-media-qualification-evidence.json"
sha256sum "$candidate/ecobin-orangepi-zero3-0.1.0-single-card.20260910.37.img"
printf 'v37-candidate-build=PASS gitCommit=%s\n' "$commit"
