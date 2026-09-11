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
        local[identity] = path
pattern = re.compile(r'^([a-zA-Z0-9._-]+)==([^ ;\\]+)(.*)$', re.MULTILINE)
reused = []
def replace(match):
    name, version, suffix = match.groups()
    path = local.get((name.replace('_', '-').lower(), version))
    if path is None:
        return match.group(0)
    reused.append(name)
    return f'{name} @ {path.as_uri()}{suffix}'
content = pattern.sub(replace, requirements.read_text())
assert reused, 'No locked cached requirement matched'
requirements.write_text(content)
print('locked-local-wheel-requirements=' + ','.join(sorted(reused)))
