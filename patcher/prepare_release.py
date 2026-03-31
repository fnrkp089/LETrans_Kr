"""
릴리즈 에셋 준비 스크립트
GitHub Release에 올릴 파일들을 생성한다:
1. 패치 번들 zip (LELocalePatch.exe + 번역 JSON)
2. SHA256SUMS (체크섬)
3. (선택) 델타 패치 (.patch)

Usage:
    python prepare_release.py <json_folder> <lelocale_exe> [--prev-bundle <old_bundle>]
"""

import os
import sys
import json
import shutil
import hashlib
import zipfile
import argparse
from pathlib import Path

try:
    import detools
    HAS_DETOOLS = True
except ImportError:
    HAS_DETOOLS = False


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_patch_bundle(
    json_folder: str,
    lelocale_exe: str,
    output_zip: str,
    version: str,
) -> str:
    """
    패치 번들 zip 생성:
    kr-patch-vX.X.X.zip/
    ├── LELocalePatch.exe
    ├── translations/
    │   ├── Skills.json
    │   ├── Properties.json
    │   └── ...
    └── manifest.json
    """
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # LELocalePatch.exe
        zf.write(lelocale_exe, f"kr-patch-{version}/LELocalePatch.exe")

        # 번역 JSON 파일들
        json_dir = Path(json_folder)
        json_files = list(json_dir.glob("*.json"))
        for jf in json_files:
            zf.write(str(jf), f"kr-patch-{version}/translations/{jf.name}")

        # 매니페스트
        manifest = {
            "version": version,
            "files": [jf.name for jf in json_files],
            "total_keys": sum(
                len(json.loads(jf.read_text("utf-8")))
                for jf in json_files
                if jf.suffix == ".json"
            ),
        }
        zf.writestr(
            f"kr-patch-{version}/manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False),
        )

    print(f"✅ 패치 번들 생성: {output_zip}")
    print(f"   JSON 파일: {len(json_files)}개")
    return output_zip


def create_delta_patch(
    old_bundle: str,
    new_bundle: str,
    output_patch: str,
) -> str | None:
    """이전 번들 → 새 번들 간 바이너리 델타 생성."""
    if not HAS_DETOOLS:
        print("⚠️ detools 미설치 — 델타 패치 생성 스킵")
        print("   pip install detools")
        return None

    try:
        with open(old_bundle, "rb") as fo, \
             open(new_bundle, "rb") as fn, \
             open(output_patch, "wb") as fp:
            detools.create_patch(fo, fn, fp, algorithm="bsdiff")

        old_size = os.path.getsize(old_bundle)
        new_size = os.path.getsize(new_bundle)
        delta_size = os.path.getsize(output_patch)
        ratio = delta_size / new_size * 100

        print(f"✅ 델타 패치 생성: {output_patch}")
        print(f"   이전: {old_size / 1024:.0f}KB → 새: {new_size / 1024:.0f}KB")
        print(f"   델타: {delta_size / 1024:.0f}KB ({ratio:.1f}%)")
        return output_patch
    except Exception as e:
        print(f"❌ 델타 패치 생성 실패: {e}")
        return None


def create_checksums(files: list[str], output: str) -> str:
    """SHA256SUMS 파일 생성."""
    with open(output, "w") as f:
        for filepath in files:
            h = sha256_file(filepath)
            name = os.path.basename(filepath)
            f.write(f"{h}  {name}\n")
            print(f"   {h[:16]}...  {name}")

    print(f"✅ 체크섬 파일: {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description="릴리즈 에셋 준비")
    parser.add_argument("json_folder", help="번역 JSON 폴더 경로")
    parser.add_argument("lelocale_exe", help="LELocalePatch.exe 경로")
    parser.add_argument("--version", "-v", required=True, help="릴리즈 버전 (e.g. v1.0.0)")
    parser.add_argument("--output-dir", "-o", default="./release", help="출력 폴더")
    parser.add_argument("--prev-bundle", help="이전 번들 파일 (델타 패치용)")
    parser.add_argument("--new-bundle", help="새 번들 파일 (델타 패치용)")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 50}")
    print(f"  릴리즈 에셋 준비 — {args.version}")
    print(f"{'=' * 50}\n")

    release_files = []

    # 1. 패치 번들
    zip_name = f"kr-patch-{args.version}.zip"
    zip_path = str(out / zip_name)
    create_patch_bundle(args.json_folder, args.lelocale_exe, zip_path, args.version)
    release_files.append(zip_path)

    # 2. 델타 패치
    if args.prev_bundle and args.new_bundle:
        delta_name = f"kr-delta-{args.version}.patch"
        delta_path = str(out / delta_name)
        result = create_delta_patch(args.prev_bundle, args.new_bundle, delta_path)
        if result:
            release_files.append(result)

    # 3. 체크섬
    print()
    checksum_path = str(out / "SHA256SUMS")
    create_checksums(release_files, checksum_path)
    release_files.append(checksum_path)

    print(f"\n{'=' * 50}")
    print(f"  릴리즈 준비 완료!")
    print(f"  출력 폴더: {out}")
    print(f"  파일: {len(release_files)}개")
    print(f"\n  gh release create {args.version} {' '.join(os.path.basename(f) for f in release_files)}")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
