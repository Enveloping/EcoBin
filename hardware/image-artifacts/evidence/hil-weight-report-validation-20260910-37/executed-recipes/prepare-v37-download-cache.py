"""Reuse locked v35 wheels and persist download caches outside disposable builders."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib
from urllib.parse import urlsplit

controlled = Path('/var/lib/ecobin-image-factory/hil-v37-20260910-snapshot')
repository = controlled / 'repository'
lock_path = repository / 'hardware/uv.lock'
lock = tomllib.loads(lock_path.read_text())
lock_sha = hashlib.sha256(lock_path.read_bytes()).hexdigest()
cache = Path('/var/cache/ecobin-image-builder') / ('arm64-py311-' + lock_sha[:16])
cache.mkdir(mode=0o700, parents=True, exist_ok=True)
assert cache.is_dir() and not cache.is_symlink() and cache.stat().st_uid == 0
cache.chmod(0o711)  # Let container _apt traverse only its non-secret downloads.
for name in ('wheels', 'pip', 'uv', 'apt', 'target-debs'):
    (cache / name).mkdir(mode=0o700, exist_ok=True)
    assert not (cache / name).is_symlink()
    (cache / name).chmod(0o755 if name in ('apt', 'target-debs') else 0o700)

archive = Path('/var/lib/ecobin-image-factory/hil-v35-20260910-snapshot/runtime/hardware-runtime-20260910-35/ecobin-hardware-hardware-runtime-20260910-35.tar.gz')
expected_archive = 'bf96a7ce54bdaa91123a949ea4190e16f9f57a05cc5c9320b5c2c247648acedf'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected_archive
allowed = {}
for package in lock['package']:
    for wheel in package.get('wheels', []):
        allowed[Path(urlsplit(wheel['url']).path).name] = wheel
reused = []
excluded = []
with tarfile.open(archive) as stream:
    for member in stream:
        path = Path(member.name)
        if path.parent.name != 'wheelhouse':
            continue
        assert member.isfile() and not member.issym() and not member.islnk()
        if path.name not in allowed:
            excluded.append(path.name)
            continue
        expected = allowed[path.name]
        content = stream.extractfile(member).read()
        digest = hashlib.sha256(content).hexdigest()
        assert len(content) == expected['size'] and 'sha256:' + digest == expected['hash']
        destination = cache / 'wheels' / path.name
        if destination.exists():
            assert not destination.is_symlink() and destination.read_bytes() == content
        else:
            destination.write_bytes(content)
            destination.chmod(0o644)
        reused.append({'file': path.name, 'bytes': len(content), 'sha256': digest})
assert len(reused) == 16

# Retain the original signed-snapshot/package/version checks. Only download
# storage and the frozen dependency installation source change.
base = controlled / 'bootstrap-builder-offline-uv.sh'
original = base.read_text()
hook = '''
# Local HIL cache: private persistent storage, never copied into the image.
rm -f -- /etc/apt/apt.conf.d/docker-clean
apt_options+=(-o Dir::Cache::archives=/ecobin-cache/apt)
bootstrap_apt_options+=(-o Dir::Cache::archives=/ecobin-cache/apt)
export PIP_CACHE_DIR=/ecobin-cache/pip
export PIP_FIND_LINKS=/ecobin-cache/wheels
export UV_CACHE_DIR=/ecobin-cache/uv
export UV_LINK_MODE=copy
export UV_PYTHON_DOWNLOADS=never
'''
assert original.count('apt-get "${bootstrap_apt_options[@]}" update') == 1
modified = original.replace('apt-get "${bootstrap_apt_options[@]}" update', hook + '\napt-get "${bootstrap_apt_options[@]}" update')
modified = modified.replace('target_deb_directory="/tmp/ecobin-target-debs"', 'target_deb_directory="/ecobin-cache/target-debs"')
modified = modified.replace('mkdir -m 0755 -- "${target_deb_directory}"', 'mkdir -p -m 0755 -- "${target_deb_directory}"')
modified = modified.replace('apt-get clean\n', '# Preserve validated package downloads for the next disposable builder.\n')
modified = modified.replace('exec bash "${script_directory}/build-software-payload.sh" "$@"', 'exec bash /builder-payload-cache.sh "$@"')
payload_source = repository / 'tools/orangepi-image/build-software-payload.sh'
payload = payload_source.read_text()
old = '''    VIRTUAL_ENV="${destination}" UV_PROJECT_ENVIRONMENT="${destination}" \\
        PYTHONDONTWRITEBYTECODE=1 \\
        uv sync --project "${repository_root}/hardware" --active --frozen \\
        --only-group "${dependency_group}" --no-install-project'''
new = '''    local frozen_requirements
    frozen_requirements="$(mktemp /tmp/ecobin-frozen-requirements.XXXXXXXX.txt)"
    uv export --project "${repository_root}/hardware" --frozen \\
        --only-group "${dependency_group}" --no-emit-project --no-header \\
        --format requirements-txt --output-file "${frozen_requirements}" >/dev/null
    # Resolve from the same frozen lock; cached wheels still require its hashes.
    uv pip install --python "${destination}/bin/python" --require-hashes \\
        --find-links /ecobin-cache/wheels --requirement "${frozen_requirements}"
    uv pip check --python "${destination}/bin/python"
    rm -f -- "${frozen_requirements}"'''
assert payload.count(old) == 1
payload = payload.replace(old, new)
directory_line = 'script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
assert payload.count(directory_line) == 1
payload = payload.replace(directory_line, 'script_directory=/workspace/tools/orangepi-image')
for name, content in (('bootstrap-builder-download-cache.sh', modified), ('build-software-payload-cache.sh', payload)):
    destination = controlled / name
    assert not destination.exists()
    destination.write_text(content)
    destination.chmod(0o700)
    subprocess.run(['bash', '-n', str(destination)], check=True)
record = {
    'purpose': 'HIL_PERSISTENT_LOCK_VERIFIED_DOWNLOAD_CACHE',
    'cacheRoot': str(cache), 'uvLockSha256': lock_sha,
    'previousRuntimeArchiveSha256': expected_archive,
    'reusedLockedWheels': reused, 'excludedNonLockWheels': excluded,
    'sourceCheckoutModified': False,
    'frozenResolutionAndRequiredHashes': True,
    'aptSignedSnapshotAndPackageVersionChecksRetained': True,
    'sourcePayloadRecipeSha256': hashlib.sha256(payload_source.read_bytes()).hexdigest(),
    'executedPayloadRecipeSha256': hashlib.sha256(payload.encode()).hexdigest(),
    'sourceBootstrapRecipeSha256': hashlib.sha256(base.read_bytes()).hexdigest(),
    'executedBootstrapRecipeSha256': hashlib.sha256(modified.encode()).hexdigest(),
}
(controlled / 'download-cache-evidence.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'cacheRoot': str(cache), 'reusedWheels': len(reused), 'reusedBytes': sum(item['bytes'] for item in reused), 'excluded': excluded}))
