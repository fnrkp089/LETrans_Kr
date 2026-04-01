"""
릴리즈 에셋 준비 스크립트
GitHub Release에 올릴 파일들을 생성.

Usage:
    py prepare_release.py <json_folder> <lelocale_exe> -v <version>

Example:
    py prepare_release.py "ko_fix_origin" "LELocalePatch.exe" -v v0.3.0
"""

import os
import sys
import json
import hashlib
import zipfile
import argparse
from pathlib import Path


def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_patch_bundle(json_folder, lelocale_exe, output_zip, version):
    """
    패치 번들 zip 생성. 구조:
    kr-patch-vX.X.X.zip 안에:
      LELocalePatch.exe
      Skills_ko.json
      Properties_ko.json
      ...
    (서브폴더 없이 플랫하게!)
    """
    json_dir = Path(json_folder)
    json_files = list(json_dir.glob("*.json"))

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        # LELocalePatch.exe
        zf.write(lelocale_exe, "LELocalePatch.exe")

        # 번역 JSON 파일들 (루트에 바로)
        for jf in json_files:
            zf.write(str(jf), jf.name)

    print(f"✅ 패치 번들 생성: {output_zip}")
    print(f"   JSON 파일: {len(json_files)}개")
    print(f"   LELocalePatch.exe 포함")
    return output_zip


def create_checksums(files, output):
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
    parser.add_argument("--version", "-v", required=True, help="릴리즈 버전 (e.g. v0.3.0)")
    parser.add_argument("--output-dir", "-o", default="./release", help="출력 폴더")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 50}")
    print(f"  릴리즈 에셋 준비 — {args.version}")
    print(f"{'=' * 50}\n")

    release_files = []

    # 패치 번들
    zip_name = f"kr-patch-{args.version}.zip"
    zip_path = str(out / zip_name)
    create_patch_bundle(args.json_folder, args.lelocale_exe, zip_path, args.version)
    release_files.append(zip_path)

    # 체크섬
    print()
    checksum_path = str(out / "SHA256SUMS")
    create_checksums(release_files, checksum_path)
    release_files.append(checksum_path)

    print(f"\n{'=' * 50}")
    print(f"  완료! 출력: {out}")
    print(f"  파일: {len(release_files)}개")
    print(f"\n  GitHub에서 새 릴리즈 만들고 이 파일들 올리면 끝!")
    print(f"{'=' * 50}\n")


if __name__ == "__main__":
    main()
