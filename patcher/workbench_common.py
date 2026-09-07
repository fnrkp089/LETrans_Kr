"""Shared file safety and locale validation. Standard library only.

Curly-braced words in Last Epoch are often localized emphasis, not immutable
format arguments. Only numeric format arguments are compared by exact identity.
"""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

DELETE = object()

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def file_hash(path):
    path = Path(path)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'중복 JSON 키: {key}')
        result[key] = value
    return result


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream, object_pairs_hook=_unique_pairs)


def read_locale(path):
    data = read_json(path)
    if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError(f'문자열 키/값 JSON이 아님: {path}')
    return data


def locale_files(folder, language='ko', required=True):
    folder = Path(folder)
    files = sorted(folder.glob(f'*_{language}.json'))
    if required and (not folder.is_dir() or not files):
        raise ValueError(f'번역 파일 없음: {folder} (*_{language}.json)')
    return files


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode('utf-8')
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


@contextmanager
def workspace_lock(root):
    """Never steal an existing lock; a crashed process requires explicit recovery."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / '.workbench.lock'
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(f'다른 작업 실행 중이거나 중단된 잠금이 있음: {path}') from None
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'pid': os.getpid(), 'started': datetime.now(timezone.utc).isoformat()}, stream)
        yield
    finally:
        path.unlink(missing_ok=True)


def commit_json(changes, root, label='edit', expected=None):
    """Validate/backup every file before writing; rollback ordinary failures.

Each replacement is atomic, not the whole set. The durable backup manifest also
supports recovery after power loss. Callers should pass hashes captured at read
time to reject stale edits. No replacement means no backup or log noise.
"""
    root = Path(root).resolve()
    encoded = {}
    for path, data in changes.items():
        path = Path(path).resolve()
        path.relative_to(root)  # refuse paths outside the workspace
        encoded[path] = None if data is DELETE else (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    expected = {Path(p).resolve(): h for p, h in (expected or {}).items()}
    with workspace_lock(root):
        for path, old_hash in expected.items():
            if file_hash(path) != old_hash:
                raise RuntimeError(f'읽은 뒤 파일이 변경됨; 다시 비교 필요: {path}')
        encoded = {p: b for p, b in encoded.items()
                   if (p.exists() if b is None else not p.exists() or p.read_bytes() != b)}
        if not encoded:
            return None
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        backup = root / 'backups' / f'{stamp}_{label}_{uuid.uuid4().hex[:6]}'
        backup.mkdir(parents=True)
        records = []
        for path, content in encoded.items():
            relative = path.relative_to(root).as_posix()
            existed = path.exists()
            if existed:
                dest = backup / 'files' / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            records.append({'path': relative, 'existed': existed,
                            'before': file_hash(path), 'after': hashlib.sha256(content).hexdigest() if content is not None else None})
        manifest = {'version': 1, 'status': 'prepared', 'files': records}
        write_json(backup / 'manifest.json', manifest)
        try:
            for path, content in encoded.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content)
        except BaseException:
            for record in records:
                path = root / record['path']
                if record['existed']:
                    atomic_write(path, (backup / 'files' / record['path']).read_bytes())
                else:
                    path.unlink(missing_ok=True)
            manifest['status'] = 'rolled_back'
            write_json(backup / 'manifest.json', manifest)
            raise
        manifest['status'] = 'committed'
        write_json(backup / 'manifest.json', manifest)
        return backup


def save_locale(path, data):
    path = Path(path)
    root = path.parent.parent if path.parent.name.startswith(('ko_', 'en_')) else path.parent
    return commit_json({path: data}, root, 'edit')


def numeric_placeholders(text):
    # Ignore escaped {{0}}. Preserve numeric ID plus alignment/format specifier.
    return Counter(re.findall(r'(?<!\{)\{\d+(?:,[^{}:]+)?(?::[^{}]+)?\}(?!\})', text))


def validate_translation(en, ko):
    errors = []
    if not isinstance(ko, str):
        return ['번역이 문자열이 아님']
    if en.strip() and not ko.strip():
        errors.append('빈 번역')
    if numeric_placeholders(en) != numeric_placeholders(ko):
        errors.append('숫자 플레이스홀더 불일치')
    return errors


def validate_dataset(en_dir, ko_dir):
    """Structural errors with content fingerprints for reviewed baseline exceptions."""
    issues = []
    ens = locale_files(en_dir, 'en')
    locale_files(ko_dir, 'ko')
    for en_path in ens:
        en = read_locale(en_path)
        name = en_path.name.removesuffix('_en.json') + '_ko.json'
        path = Path(ko_dir) / name
        ko = read_locale(path) if path.exists() else {}
        for key, original in en.items():
            errors = ['키 누락'] if key not in ko else validate_translation(original, ko[key])
            for error in errors:
                issue = {'file': name, 'key': key, 'error': error,
                         'en': original, 'ko': ko.get(key)}
                issue['id'] = digest(issue)
                issues.append(issue)
    # Malformed extra locale files must not evade validation.
    for path in locale_files(ko_dir):
        read_locale(path)
    return issues


def split_known_issues(issues, baseline=None):
    known = set()
    if baseline:
        manifest = read_json(Path(baseline) / 'snapshot.json')
        known = {i['id'] for i in manifest['known_issues']}
    return [i for i in issues if i['id'] not in known], [i for i in issues if i['id'] in known]


def replace_korean(text, old, new, particles=False):
    if not old:
        raise ValueError('빈 검색어로 치환할 수 없음')
    if not particles or not new or not ('가' <= new[-1] <= '힣'):
        return text.replace(old, new)
    jong = (ord(new[-1]) - 0xAC00) % 28
    mapping = {'을': '을' if jong else '를', '를': '을' if jong else '를',
               '은': '은' if jong else '는', '는': '은' if jong else '는',
               '이': '이' if jong else '가', '가': '이' if jong else '가',
               '과': '과' if jong else '와', '와': '과' if jong else '와',
               '으로': '으로' if jong not in (0, 8) else '로',
               '로': '으로' if jong not in (0, 8) else '로'}
    # Only a matched replacement's immediate suffix; do not guess dynamic {0} josa.
    pattern = re.compile(re.escape(old) + r'(\})?(으로|로|을|를|은|는|이|가|과|와)?(?=$|[^가-힣])')
    def sub(match):
        close, particle = match.groups()
        return new + (close or '') + mapping.get(particle, '')
    # All occurrences must still be replaced, including compounds without particles.
    pieces = []; end = 0
    for match in re.finditer(re.escape(old), text):
        if match.start() < end:
            continue
        pieces.append(text[end:match.start()])
        suffix = pattern.match(text, match.start())
        if suffix:
            pieces.append(sub(suffix)); end = suffix.end()
        else:
            pieces.append(new); end = match.end()
    return ''.join(pieces) + text[end:]
