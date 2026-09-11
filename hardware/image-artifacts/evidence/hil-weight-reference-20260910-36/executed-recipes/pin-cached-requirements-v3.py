"""Replace only exact hash-verified locked requirements with local wheel URLs."""
import hashlib
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import urlsplit

requirements, lock_file, wheel_root = map(Path, sys.argv[1:])
lock = tomllib.loads(lock_file.read_text())
local = {}
for package in lock['package']:
    for item in package.get('wheels', []):
        name = Path(urlsplit(item['url']).path).name
        path = wheel_root / name
        if not path.exists():
            continue
        assert path.is_file() and not path.is_symlink()
        assert path.stat().st_size == item['size']
        assert 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() == item['hash']
        identity = (package['name'].replace('_', '-').lower(), package['version'])
        assert identity not in local, 'Ambiguous cached wheel platform'
        local[identity] = (path, item['hash'])
pattern = re.compile(r'^([a-zA-Z0-9._-]+)==([^ ;\\]+)(.*)$')
reused = []
lines = iter(requirements.read_text().splitlines(keepends=True))
result = []
for line in lines:
    if not pattern.match(line):
        result.append(line)
        continue
    block = line
    while line.rstrip().endswith('\\'):
        line = next(lines)
        block += line
    logical = re.sub(r'\\\n\s*', ' ', block).strip()
    head = logical.split(' --hash=', 1)[0].rstrip()
    match = pattern.fullmatch(head)
    assert match
    name, version, suffix = match.groups()
    cached = local.get((name.replace('_', '-').lower(), version))
    if cached is None:
        result.append(block)
        continue
    path, exact_hash = cached
    assert exact_hash in re.findall(r'--hash=(sha256:[0-9a-f]{64})', logical)
    reused.append(name)
    # A direct local artifact has one permitted digest, taken from the frozen
    # lock. Narrowing avoids uv 0.12.5's local-URL multi-hash mismatch.
    result.append(f'{name} @ {path.as_uri()}{suffix} --hash={exact_hash}\n')
content = ''.join(result)
assert reused, 'No locked cached requirement matched'
requirements.write_text(content)
print('locked-local-wheel-requirements=' + ','.join(sorted(reused)))
