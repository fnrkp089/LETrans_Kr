"""Checked LELocalePatch execution; stage imports beside a copy of the catalog."""
from datetime import datetime
from pathlib import Path
import argparse
import shutil
import subprocess
import tempfile

from workbench_common import atomic_write, file_hash, locale_files, read_locale, write_json, workspace_lock


def run_checked(executable, bundle, action, target, timeout=300):
    executable, bundle, target = map(lambda p: Path(p).resolve(), (executable, bundle, target))
    if not executable.is_file() or not bundle.is_file():
        raise FileNotFoundError('LELocalePatch.exe 또는 번들 파일 없음')
    result = subprocess.run([str(executable), str(bundle), action, str(target)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=timeout)
    lines = [line.strip() for line in result.stdout.splitlines()]
    # Upstream Main catches errors without setting a nonzero exit code.
    if result.returncode or 'Error' in lines or 'Done!' not in lines:
        raise RuntimeError(f'LELocalePatch 실패: {result.stderr or result.stdout}')
    return result


def export_locale(executable, bundle, destination, language):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with workspace_lock(destination.parent):
        with tempfile.TemporaryDirectory(prefix='.locale-export-', dir=destination.parent) as tmp:
            stage = Path(tmp) / 'export'
            run_checked(executable, bundle, 'export', stage)
            files = locale_files(stage, language)
            for path in files:
                read_locale(path)
            archive = None
            if destination.exists():
                archive = destination.parent / 'backups' / ('export_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f')) / destination.name
                archive.parent.mkdir(parents=True)
                destination.rename(archive)
            try:
                stage.rename(destination)
            except BaseException:
                if archive:
                    archive.rename(destination)
                raise
    return len(files)


def import_locale(executable, bundle, source):
    bundle, source = Path(bundle).resolve(), Path(source).resolve()
    if not bundle.is_file():
        raise FileNotFoundError(bundle)
    inputs = {path.name: read_locale(path) for path in locale_files(source)}
    catalog = next((bundle.parent.parent / name for name in ['catalog.bin', 'catalog.json', 'catalog.bundle']
                    if (bundle.parent.parent / name).is_file()), None)
    if catalog is None:
        raise FileNotFoundError('번들의 상위 폴더에서 catalog.bin/json/bundle을 찾을 수 없음')
    original_hashes = {bundle: file_hash(bundle), catalog: file_hash(catalog)}
    # Snapshot source JSON as well; edits during the external process cannot leak in.
    with tempfile.TemporaryDirectory(prefix='le-import-') as tmp:
        root = Path(tmp); staged_bundle = root / bundle.parent.name / bundle.name
        staged_bundle.parent.mkdir()
        staged_catalog = root / catalog.name
        shutil.copy2(bundle, staged_bundle); shutil.copy2(catalog, staged_catalog)
        staged_source = root / 'input'; staged_source.mkdir()
        for name, data in inputs.items():
            write_json(staged_source / name, data)
        run_checked(executable, staged_bundle, 'import', staged_source)
        exported = root / 'check'
        run_checked(executable, staged_bundle, 'export', exported)
        outputs = {p.name: read_locale(p) for p in locale_files(exported)}
        applied = 0
        for name, data in outputs.items():
            if name not in inputs:
                raise ValueError(f'현재 게임 테이블에 대한 번역 파일 누락: {name}')
            for key in data.keys() & inputs[name].keys():
                if data[key] != inputs[name][key]:
                    raise ValueError(f'적용 후 검증 실패: {name}:{key}')
                applied += 1
        if not applied:
            raise ValueError('게임 번들과 일치하는 번역 키 없음')
        with workspace_lock(bundle.parent.parent):
            if any(file_hash(p) != h for p, h in original_hashes.items()):
                raise RuntimeError('적용 준비 중 게임 파일이 변경됨. 다시 실행 필요')
            backup = bundle.parent.parent / 'kr_workbench_backups' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup.mkdir(parents=True)
            for path in original_hashes:
                shutil.copy2(path, backup / path.name)
            write_json(backup / 'manifest.json', {'files': {p.name: h for p, h in original_hashes.items()}})
            try:
                atomic_write(bundle, staged_bundle.read_bytes())
                atomic_write(catalog, staged_catalog.read_bytes())
            except BaseException:
                for path in original_hashes:
                    atomic_write(path, (backup / path.name).read_bytes())
                raise
    return applied, backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('bundle', type=Path)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--exe', type=Path, default=Path(__file__).parent / 'LELocalePatch.exe')
    parser.add_argument('--language', choices=['en', 'ko'], default='ko')
    args = parser.parse_args()
    if args.action == 'export':
        print(f'추출 완료: {export_locale(args.exe, args.bundle, args.folder, args.language)}개 파일')
    else:
        count, backup = import_locale(args.exe, args.bundle, args.folder)
        print(f'적용 검증 완료: {count}키 / 백업: {backup}')


if __name__ == '__main__':
    main()
