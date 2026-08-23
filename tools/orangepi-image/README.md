# EcoBin Orange Pi image tooling

This directory is the single build entry for the Orange Pi Zero 3 production
image. A no-secret candidate deliberately contains no K1, setup access-point
password, device credential, or signing private key. It does contain the real
runtime, factory/first-boot, enrollment and remote-support software supplied by
an independently locked ARM64 software payload.

## Current state

The official Orange Pi Debian 12 Server/Linux 6.1 source artifact is locked to
the exact 1.0.4 Google Drive object, byte length, archive SHA-256, member name,
and extracted byte length verified on 2026-08-22. The Docker Official Image
builder is locked to the `linux/arm64` manifest digest, and all builder tools
plus target additions come from the signed immutable Debian 2026-08-03
snapshot at exact versions. The qualified 32 GB media/layout and independent
qualification evidence for the deterministic ext4 rebuild are still
`UNLOCKED`/`UNQUALIFIED`. This is a safe partial-lock state:

```text
python tools/orangepi-image/lib/validate_inputs.py --allow-unlocked
# source=LOCKED builder=LOCKED apt=LOCKED layout=UNLOCKED

bash tools/orangepi-image/build-image.sh --validate-only
# fails because production inputs are not locked

python tools/orangepi-image/lib/release_trust.py validate-policy \
  --trust-policy tools/orangepi-image/formal-release-policy.json
# fails because the repository file is only an UNLOCKED template
```

Do not replace the remaining layout nulls with guessed values. The three locked
inputs prove artifact, builder and package identity; they do not claim that the
image has passed the target board or smallest qualified 32 GB card boot test.
Those facts keep the layout and production build gate locked shut until
hardware-in-the-loop qualification. Even after that qualification, a formal
candidate remains blocked until deterministic ext4 rebuild evidence is
independently approved. This tooling deliberately does not claim that the
current read-write ext4 mutation path is reproducible.

The same read-only source inspection measured the extracted member as
2,571,108,352 bytes with SHA-256
`dda562f0ce9c3fc2be656f816b8f3aea466a3f9ace9fdf87d97ebdc72aeada40`.
It has a DOS partition-table identifier `da1827ff`; partition 1 starts at sector
8192, has 5,013,504 sectors, ext4 UUID
`535922e7-511d-46ea-82e6-573f913006af`, and PARTUUID `da1827ff-01`.
These are inspection evidence, not qualified `image-layout.json` values: the
minimum real 32 GB card and target-board boot/expansion test are still missing.
The untouched base also contained a non-empty machine ID, six SSH host-key
files (private/public), and an enabled `orangepi-resize-filesystem.service`.
Consequently, merely extracting and auditing that base is expected to fail.

## Files

- `source.lock.json`: board, operating system, and exact base artifact identity.
- `builder.lock`: digest-pinned Linux builder and exact tool versions.
- `apt-packages.lock`: exact target package versions; no ranges or `latest`.
- `image-layout.json`: independently locked source geometry/ext4 build profile
  plus the still-separate 32 GB media-qualification gate.
- `schemas/target-media-qualification-evidence.schema.json`: exact same-batch
  whole-card capacity evidence contract. A formal build must provide the actual
  evidence file whose raw SHA-256 and measured minimum are locked by the layout.
- `formal-release-policy.json`: permanently fail-closed repository template.
  It must remain `UNLOCKED`, contain no key fingerprint, and cannot authorize a
  release. A locked policy is provisioned separately on each controlled
  station, outside the source repository.
- `schemas/`: machine-readable input, payload and artifact schemas.
- `build-software-payload.sh`: inner ARM64 payload builder. It verifies a
  signed hardware-runtime archive and creates three additional relocatable
  Python 3.11 environments from `hardware/uv.lock`.
- `run-payload-builder.sh`: digest-pinned outer launcher for the payload build.
- `lib/generate_software_payload_lock.py`: validates every required component,
  production batch configuration, signature-verification evidence and current
  runtime source before writing an exact file inventory.
- `build-image.sh`: canonical Linux image build entry. It extracts a verified
  base image, sanitizes a private working copy, deterministically rebuilds its
  ext4 root, assembles every output byte from the locked boot prefix and the
  rebuilt partition, and injects no device secrets.
- `rebuild-rootfs.sh` and `lib/rootfs_tree_inventory.py`: copy the staged tree
  with the exact mke2fs profile, reject unsupported inode flags/project IDs,
  compare file types, ownership, modes, contents, links, xattrs and devices,
  then normalize every reachable inode's atime/mtime/ctime/crtime and extra
  bits in numeric-inode order.
- `attest-candidates.sh`: snapshots and audits two independently built,
  byte-identical no-secret candidates and manifests. It additionally requires
  two Ed25519-signed build receipts with different invocation UUIDs, builder
  identities, builder domains and policy-pinned external receipt keys; copying
  one candidate or receipt cannot establish independent builds.
- `seal-image.sh`: snapshots one attested candidate, injects only K1 and the
  setup-hotspot passphrase, audits the result and signs file-level seal
  evidence with a separate sealing key.
- `release-image.sh`: verifies both signed proofs, audits one sealed image,
  creates one compressed release and atomically publishes its exact inventory
  signed by a third release key.
- `trusted-flash-entry.*`: source for the separately installed trusted factory
  launcher. It authenticates and snapshots the complete untrusted release
  before executing the snapshotted signed writer.
- `run-builder.sh`: production launcher. It pulls the exact locked Docker
  manifest for `linux/arm64`, mounts the repository and base artifact read-only,
  and starts a privileged disposable builder.
- `bootstrap-builder.sh`: configures only the locked Debian snapshot, installs
  every builder tool at its locked version, verifies installed package versions,
  and only then enters `build-image.sh`.
- `sanitize-candidate.sh`: attaches only a regular copied image through a loop
  device, validates the exact locked final ext4 partition, and mounts it below a
  root-only temporary directory for cleanup.
- `lib/sanitize_rootfs.py`: testable root-filesystem policy. It clears machine
  identity, SSH host keys, random/network/cloud state, logs/caches, factory and
  legacy hardware data; removes official resize/stage/simulator enable links;
  deletes getty automatic-login drop-ins; rejects automatic login embedded in a
  base getty/serial-getty unit or display-manager configuration; disables
  persistent swap and Orange Pi zram configuration; and replaces both `root` and `orangepi`
  password fields with a lock marker. The accounts
  themselves remain present. Password hashes are never printed.
- `New-EcobinOrangePiImage.ps1`: thin Windows-to-WSL build wrapper.
- `verify-image.sh`: read-only image/layout/ext4-profile/cleanliness/software
  audit, including exact UUID/PARTUUID boot bindings and absence of persistent
  swap. During
  candidate construction it compares against the external payload; later
  sealed/release audits use the immutable lock installed inside the image.
- `expand-rootfs.sh`: fail-closed, idempotent ext4 expansion helper. It binds
  the mounted root by MAJ:MIN to one direct sysfs partition and rechecks its
  disk, UUID, PARTUUID, start sector and final-partition identity around growth.
- `flash-and-verify.*`: guarded write and full written-range reread verification.
- `tests/`: host-side static and fail-closed regression tests.

## Locking workflow

1. Copy the official artifact into controlled build storage; do not build from
   an unverified network stream.
2. Fill `source.lock.json`, including byte sizes and lowercase SHA-256, then set
   `lockState` to `LOCKED`.
3. Re-qualify the checked-in digest-pinned builder and exact tools before
   changing `builder.lock`; never replace the dated tag with a rolling tag.
4. Change target packages only by resolving exact versions from a new immutable,
   signed Debian snapshot and updating `apt-packages.lock` in the same review.
5. Source raw/prefix geometry and the ext4 construction profile may be locked
   independently. Only after real 32 GB cards have been measured may
   `targetMedia` become `QUALIFIED`, receive a non-null evidence digest and
   minimum byte size, and the top-level layout become `LOCKED`. Never make the
   fixed image larger than `minimumQualifiedMediaBytes - tailSafetyBytes`.
   The supplied `target-media-qualification-evidence.json` must record at least
   two uniquely identified whole cards from the same procurement batch measured
   with `blockdev --getsize64`; the validator recomputes its raw digest and
   requires its smallest sample to equal the locked minimum.
6. Pass that evidence explicitly as
   `--target-media-qualification-evidence FILE`, then run both test entry points
   and `build-image.sh --validate-only` before a real build.

The lock validator rejects `latest`, all-zero digests, mutable builder tags,
missing sizes, unlocked package lists, and layouts that do not leave the
declared media safety margin. It also rejects any attempt to turn the
checked-in formal policy template into a trust root.

## Controlled ARM64 software payload

Do not assemble `components/` by hand. Build it in the same digest-pinned ARM64
container used by the image builder. The hardware runtime must first be built
and signed through `hardware/install/build_runtime_release.py`. All inputs below
are mandatory and mounted read-only:

```bash
tools/orangepi-image/run-payload-builder.sh \
  --output-dir /controlled/output/software-payload-001 \
  --runtime-archive /controlled/runtime/hardware-runtime.tar.gz \
  --runtime-sha256 <64-lowercase-hex> \
  --runtime-signature /controlled/runtime/hardware-runtime.sig \
  --runtime-signing-key-id factory_2026 \
  --runtime-trust-dir /controlled/trust/runtime-release-keys \
  --mcu-trust-dir /controlled/trust/mcu-release-keys \
  --enrollment-env /controlled/config/enrollment.env \
  --cellular-env /controlled/config/cellular.env \
  --payload-id software-payload-001 \
  --runtime-release-id hardware-runtime-001 \
  --enrollment-release-id enrollment-001 \
  --remote-support-release-id remote-support-001 \
  --factory-test-release-id factory-test-001 \
  --first-boot-release-id first-boot-001
```

The command prints the SHA-256 of `software-payload.lock.json`. Record that
value in the controlled release job and pass it separately to the image build.
The payload contains no K1, setup AP password or private signing key.
`enrollment.env` has exactly the production HTTPS URL, `K1` key ID and
`SELF_ENROLLMENT` mode. `cellular.env` must exactly satisfy the Air780E RNDIS
schema-v2/HIL parser; USB VID/PID remain runtime diagnostics and are forbidden
batch fields. The two trust directories may contain only bounded, regular
Ed25519 `PUBLIC KEY` PEM files named with their respective MCU or runtime key-ID
grammar. A missing venv, an unsigned/unverified runtime archive, a Git/source
mismatch, an empty component release ID, a secret/legacy config field, a
private key/certificate/extra trust file, or any modified file prevents a
`LOCKED` payload from being produced.

## Candidate build (Linux/WSL)

After all locks are proven and locked:

```bash
tools/orangepi-image/run-builder.sh \
  --source /controlled/input/Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31.7z \
  --output-dir /controlled/output/ecobin-image-p1 \
  --release-id p1-board-trial-001 \
  --version 0.1.0 \
  --software-payload /controlled/output/software-payload-001 \
  --software-payload-sha256 <payload-lock-sha256> \
  --target-media-qualification-evidence \
  /controlled/evidence/target-media-qualification-evidence.json
```

The output directory must not already exist. The builder verifies the source
artifact before extraction, refuses unexpected archive members or sizes,
proves the working image is a distinct single-link file, sanitizes its exact
root partition, rebuilds ext4 using the locked UUID/features/profile, verifies
semantic inventory and `e2fsck`, writes a candidate manifest, creates SHA-256
sidecars, then invokes the read-only image audit. The audit requires absent
persistent swap as well as empty/absent machine identity,
host keys, random seeds, cloud/DHCP history, logs, package caches, factory-test
results, `/root/EcoBin/hardware/data`, OneNet device-key configuration, and
blocked local passwords. It also rejects console/display-manager automatic
login, independently enabled stage or simulator units, and the vendor
automatic-resize unit.

The candidate installs the hardware runtime, factory/first-boot portal,
enrollment and remote-support environments, all EcoBin units, sysusers,
tmpfiles, NetworkManager policy, trust roots and batch configuration. Exactly
three safety/coordinator units are enabled: MCU safe GPIO, the whole-machine
early egress lock, and the first-boot coordinator. Enrollment, cellular,
factory stages, remote support and normal runtime remain static and are reached
only through the fact-driven first-boot state machine. Generic nftables, ufw,
firewalld, hostapd, dnsmasq and `wpa_supplicant@wlan0` units are masked because
the EcoBin units own those boundaries.

The deterministic path does not publish the read-write-mutated vendor ext4.
It treats that filesystem only as a staged semantic tree, recreates ext4 with
the locked e2fsprogs profile, normalizes all reachable inode timestamps, and
assembles a fresh raw image. `rootfsDeterministic=true` is written only after
that path and all verifiers execute. A dirty-source build remains
`releaseEligible=false`.

On Windows, run the equivalent through WSL:

```powershell
.\tools\orangepi-image\New-EcobinOrangePiImage.ps1 `
  -SourceArtifact C:\controlled\Orangepizero3_1.0.4_debian_bookworm_server_linux6.1.31.7z `
  -OutputDirectory C:\controlled\ecobin-image-p1 `
  -ReleaseId p1-board-trial-001 `
  -Version 0.1.0 `
  -SoftwarePayloadDirectory C:\controlled\software-payload-001 `
  -SoftwarePayloadLockSha256 <payload-lock-sha256> `
  -TargetMediaQualificationEvidence `
  C:\controlled\evidence\target-media-qualification-evidence.json
```

## External formal-release trust

The repository policy is deliberately incapable of trusting itself. Before a
formal release, an administrator must provision a different absolute policy
path outside the repository, for example
`/etc/ecobin-image-factory/formal-release-policy.json`. Its complete parent
chain must be root-owned and not group/other writable. A `LOCKED` policy must
pin three different Ed25519 identities (`buildAttestation`, `sealEvidence` and
`releaseSigning`) and must contain independently reviewed rootfs qualification:

```json
"rootfsQualification": {
  "state": "QUALIFIED",
  "method": "DETERMINISTIC_EXT4_REBUILD_V1",
  "evidenceSha256": "<non-zero SHA-256 of controlled qualification evidence>"
}
```

The locked external policy must also name exactly two distinct
`builderReceipts` identities/domains/key fingerprints. Each builder creates a
canonical receipt with `lib/generate_build_receipt.py`, then signs it only
through the protected entry below; a direct OpenSSL invocation is not a formal
build receipt:

```bash
sudo python3 tools/orangepi-image/lib/release_trust.py sign \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --role builderReceipt \
  --private-key /factory/keys/build-receipt-a-private.pem \
  --key-id builder_receipt_a_2026 \
  --payload /factory/proofs/build-receipt-a.json \
  --output /factory/proofs/build-receipt-a.sig
```

The entry uniquely selects one `builderReceipts` policy identity by key ID,
matches that identity/domain against the receipt, verifies the private-key
fingerprint, applies the same memory/swap and safe-snapshot policy as the three
release roles, and creates the signature without overwrite. Supply receipt,
signature and public key separately to `attest-candidates.sh`. The private key
is never stored in a candidate image. Attestation also requires the actual
`rootfs-qualification-evidence.json`; its bytes must match the policy digest
and its locked source geometry, builder/e2fsprogs identity and six deterministic
test facts are validated. The evidence and schema are members of the final
signed release inventory.

Candidate sanitization explicitly disables Orange Pi zram (`ENABLED=false`,
`SWAP=false`) and removes zram-generator/zramswap activation. Before `dd`, the
trusted flasher reads `QUALIFIED` and `minimumQualifiedMediaBytes` from the
signed release manifest and rejects smaller media.

Do not edit the checked-in template to contain these values. Public keys and
the external policy must be root-owned and not group/other writable. Private
keys, K1 and the setup-hotspot passphrase must additionally be single-link
regular files with mode `0600`. Formal output parents are pre-created as
`root:root 0700`; outputs are created without overwrite.

Every entry that handles a private key, K1 or a sealed image disables core
dumps and refuses to run while any swap device/file is active. Use a dedicated
factory station and disable swap before the operation. The scripts read each
private/secret source through one non-following file descriptor, validate that
same descriptor's owner, mode and link count, and pass only a private snapshot
to OpenSSL or the injector.

WSL2 commonly exposes a host-managed swap file. For a dedicated factory WSL
distribution, set `swap=0` under `[wsl2]` in `%UserProfile%\.wslconfig`, run
`wsl --shutdown`, restart the distribution, and confirm `swapon --show` is
empty before handling K1 or a signing key.

The checked-in build still cannot reach a formal image build: the top-level
`image-layout.json.lockState` remains `UNLOCKED` and `targetMedia` remains
`UNQUALIFIED` until real 32 GB media evidence exists. Independent rootfs
qualification in the external formal-release policy is also still required.
Signing fabricated evidence does not override either gate.

## Two-candidate attestation

Once deterministic rootfs qualification exists, build the same commit and
locked payload twice in independent fresh build directories. Then run on the
controlled attestation station (all shown output directories already exist as
`root:root 0700`):

```bash
sudo tools/orangepi-image/attest-candidates.sh \
  --candidate-a /factory/candidate-a/ecobin.img \
  --manifest-a /factory/candidate-a/image-manifest.json \
  --candidate-b /factory/candidate-b/ecobin.img \
  --manifest-b /factory/candidate-b/image-manifest.json \
  --software-payload /factory/payload/software-payload-001 \
  --software-payload-sha256 <payload-lock-sha256> \
  --release-id production-001 --version 1.0.0 \
  --git-commit <40-lowercase-hex> \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --target-media-qualification-evidence /factory/evidence/target-media-qualification-evidence.json \
  --rootfs-qualification-evidence /factory/evidence/rootfs-qualification-evidence.json \
  --receipt-a /factory/proofs/build-receipt-a.json \
  --receipt-signature-a /factory/proofs/build-receipt-a.sig \
  --receipt-public-key-a /etc/ecobin-image-factory/build-receipt-a-public.pem \
  --receipt-b /factory/proofs/build-receipt-b.json \
  --receipt-signature-b /factory/proofs/build-receipt-b.sig \
  --receipt-public-key-b /etc/ecobin-image-factory/build-receipt-b-public.pem \
  --signing-private-key /factory/keys/build-attestation-private.pem \
  --signing-public-key /etc/ecobin-image-factory/build-attestation-public.pem \
  --signing-key-id build_attestation_2026 \
  --output-attestation /factory/proofs/build-attestation.json \
  --output-signature /factory/proofs/build-attestation.sig
```

The command first snapshots both raw images, both manifests and the payload
lock into a private directory. Only those snapshots are compared, audited and
signed. The formal fact is byte identity of the two independently built
**no-secret candidates**. It is not a claim that secret-bearing sealed images
are reproducible.

## Protected factory sealing (P6)

`seal-image.sh` creates one controlled snapshot of an attested candidate,
audits those exact no-secret bytes, then injects K1 and the shared WPA2
passphrase as `root:root 0600`. It audits the sealed result and signs canonical
file-level evidence with the separate sealing key. Neither secret nor its
individual digest is printed or written into evidence.

```bash
sudo tools/orangepi-image/seal-image.sh \
  --candidate /factory/candidate-a/ecobin.img \
  --output /factory/sealed/ecobin-sealed.img \
  --build-attestation /factory/proofs/build-attestation.json \
  --build-attestation-signature /factory/proofs/build-attestation.sig \
  --build-attestation-public-key /etc/ecobin-image-factory/build-attestation-public.pem \
  --software-payload-sha256 <payload-lock-sha256> \
  --release-id production-001 --version 1.0.0 \
  --git-commit <40-lowercase-hex> \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --target-media-qualification-evidence /factory/evidence/target-media-qualification-evidence.json \
  --enrollment-key-file /factory/keys/enrollment-k1 \
  --setup-ap-key-file /factory/keys/setup-ap.key \
  --sealing-private-key /factory/keys/seal-evidence-private.pem \
  --sealing-public-key /etc/ecobin-image-factory/seal-evidence-public.pem \
  --sealing-key-id seal_evidence_2026 \
  --evidence /factory/sealed/seal-evidence.json \
  --evidence-signature /factory/sealed/seal-evidence.sig
```

Do not pass secret files from Windows `/mnt/c`: that mount generally cannot
prove POSIX root ownership and owner-only mode. The sealed raw image, its
compressed release and every written TF card contain K1 until successful
enrollment removes the logical file, so all are secret-bearing assets.
Deletion cannot guarantee physical erasure on SSD, flash, copy-on-write or
wear-levelled TF media. Use an encrypted dedicated scratch volume and destroy
its encryption key after the batch; quarantine or physically destroy rejected
cards when K1 exposure matters.

## Signed release creation

One sealed image plus its signed seal evidence is sufficient. Rebuilding two
sealed images and comparing their bytes is neither required nor meaningful.
The release command authenticates the two-candidate proof, authenticates the
seal evidence, repeats the read-only sealed audit, generates metadata twice,
and publishes the whole release directory with one same-filesystem rename:

```bash
sudo tools/orangepi-image/release-image.sh \
  --sealed-image /factory/sealed/ecobin-sealed.img \
  --seal-evidence /factory/sealed/seal-evidence.json \
  --seal-evidence-signature /factory/sealed/seal-evidence.sig \
  --seal-evidence-public-key /etc/ecobin-image-factory/seal-evidence-public.pem \
  --build-attestation /factory/proofs/build-attestation.json \
  --build-attestation-signature /factory/proofs/build-attestation.sig \
  --build-attestation-public-key /etc/ecobin-image-factory/build-attestation-public.pem \
  --target-media-qualification-evidence /factory/evidence/target-media-qualification-evidence.json \
  --rootfs-qualification-evidence /factory/evidence/rootfs-qualification-evidence.json \
  --candidate-manifest /factory/candidate-a/image-manifest.json \
  --trust-policy /etc/ecobin-image-factory/formal-release-policy.json \
  --signing-private-key /factory/keys/release-signing-private.pem \
  --signing-public-key /etc/ecobin-image-factory/release-signing-public.pem \
  --signing-key-id release_2026 \
  --output-dir /factory/releases/ecobin-orangepi-zero3-1.0.0
```

Power loss before the final rename can leave only a uniquely named hidden
staging directory, which does not satisfy or block the requested final release
name. Power loss after the rename leaves the complete signed inventory; a raw
payload and detached signature are never published as an accepted partial
release pair.

## Trusted card writing

Never start with `flash-and-verify.sh` from an untrusted release directory: its
own signature cannot establish the trustworthiness of the program performing
that first verification. Install the trusted launcher and its trust helper
separately, owned by root and not writable by the release operator:

```text
/usr/local/lib/ecobin-image-factory/trusted-flash-entry.sh
/usr/local/lib/ecobin-image-factory/lib/release_trust.py
/etc/ecobin-image-factory/formal-release-policy.json
/etc/ecobin-image-factory/release-signing-public.pem
/var/lib/ecobin-image-factory/flash-staging/   # root:root 0700
```

The trusted entry first snapshots the checksum file and signature, verifies the
signature against the external locked policy, validates the exact 19-member
inventory, then snapshots every signed member (including the compressed image,
target-media/rootfs evidence and writer) into a private root directory. It
executes only the snapshotted writer. No program or metadata from the supplied
release path runs before this authentication and snapshot complete.

Verification without writing:

```bash
sudo /usr/local/lib/ecobin-image-factory/trusted-flash-entry.sh \
  --release-dir /factory/releases/ecobin-orangepi-zero3-1.0.0 \
  --signing-key-id release_2026 --verify-only
```

Destructive write requires the same explicit removable block-device path twice:

```bash
sudo /usr/local/lib/ecobin-image-factory/trusted-flash-entry.sh \
  --release-dir /factory/releases/ecobin-orangepi-zero3-1.0.0 \
  --signing-key-id release_2026 \
  --device /dev/sdX --confirm-device /dev/sdX
```

The signed writer fully decompresses and hashes the raw image before any write,
refuses mounted/non-removable media and the disk backing `/`, writes only the
image range, then rereads that complete range. A power interruption leaves a
uniquely named private snapshot; a later retry creates a new snapshot and does
not execute the stale one.

On Windows, install `trusted-flash-entry.ps1` as separately controlled factory
tooling and invoke it with an explicitly mapped WSL block device. The launcher
uses the fixed WSL trusted path above, never a script from the release:

```powershell
& 'C:\Program Files\EcoBin Image Factory\trusted-flash-entry.ps1' `
  -ReleaseDirectory C:\factory\releases\ecobin-orangepi-zero3-1.0.0 `
  -SigningKeyId release_2026 `
  -WslDevice /dev/sdX -ConfirmWslDevice /dev/sdX
```

The PowerShell launcher does not guess a Windows physical disk. Trial media
remains restricted to controlled, recoverable factory use. A production-card
release additionally requires the deterministic-rootfs gate, two independent
identical no-secret builds, protected sealing and the signed release inventory.
