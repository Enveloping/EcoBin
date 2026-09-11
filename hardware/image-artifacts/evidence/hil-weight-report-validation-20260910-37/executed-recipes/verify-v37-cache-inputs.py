"""Exercise real locked requirements and reject a corrupted isolated cache copy."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path('/var/lib/ecobin-image-factory/hil-v37-20260910-snapshot')
script = root / 'pin-cached-requirements-v3.py'
lock = root / 'repository/hardware/uv.lock'
cache = Path('/var/cache/ecobin-image-builder/arm64-py311-9d42ea99614e3763/wheels')
source = Path('/var/lib/ecobin-image-factory/hil-v35-20260910-snapshot/software-payload-20260910-35/components/hardware-runtime/requirements-runtime.txt')
original = source.read_text()
source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory(prefix='ecobin-v37-cache-check-') as tmp:
    temporary = Path(tmp)
    requirements = temporary / 'requirements.txt'
    requirements.write_text(original)
    result = subprocess.run(['python3', str(script), str(requirements), str(lock), str(cache)], capture_output=True, text=True, check=True)
    rewritten = requirements.read_text()
    assert rewritten.count(' @ file:///') == 16
    original_hashes = set(re.findall(r'--hash=sha256:[0-9a-f]+', original))
    assert set(re.findall(r'--hash=sha256:[0-9a-f]+', rewritten)) <= original_hashes
    for line in rewritten.splitlines():
        if ' @ file:///' in line:
            assert line.count('--hash=sha256:') == 1
    assert 'crcmod==1.7' in rewritten and 'crcmod @ ' not in rewritten
    corrupt = temporary / 'corrupt-cache'
    corrupt.mkdir()
    (corrupt / 'certifi-2026.5.20-py3-none-any.whl').write_bytes(b'corrupted')
    requirements.write_text(original)
    rejected = subprocess.run(['python3', str(script), str(requirements), str(lock), str(corrupt)], capture_output=True, text=True)
    assert rejected.returncode != 0 and requirements.read_text() == original
assert hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
record = {'lockedWheelPins': 16, 'permittedDigestsNarrowedToExactFrozenLockArtifact': True,
          'nonLockBuiltCrcmodWheelExcluded': True, 'corruptCacheRejectedBeforeRewrite': True,
          'originalRequirementsUnchanged': True, 'result': 'PASS'}
(root / 'cache-input-verification-v3.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
