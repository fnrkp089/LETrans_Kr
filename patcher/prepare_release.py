"""
릴리즈 에셋 준비 스크립트
GitHub Release에 올릴 파일들 + 릴리즈 노트 자동 생성.

Usage:
    py prepare_release.py "ko_fix_origin" "LELocalePatch.exe" -v v0.4.2

Example:
    py prepare_release.py "ko_fix_origin" "LELocalePatch.exe" -v v0.4.2
"""

import os
import re
import tempfile
import shutil
from workbench_common import locale_files, read_locale, validate_dataset, split_known_issues, write_json
import sys
import json
import hashlib
import zipfile
import argparse
import subprocess
import ssl
import urllib.request
from pathlib import Path
from datetime import datetime

GITHUB_REPO = "fnrkp089/LETrans_Kr"

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        SSL_CONTEXT = ssl.create_default_context()
    except Exception:
        raise RuntimeError("TLS 인증서 초기화 실패. 인증서 설정 확인 필요")


def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def create_patch_bundle(json_folder, lelocale_exe, output_zip, version):
    json_dir = Path(json_folder)
    json_files = locale_files(json_dir)
    for path in json_files:
        read_locale(path)
    if not Path(lelocale_exe).is_file():
        raise FileNotFoundError(lelocale_exe)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(lelocale_exe, "LELocalePatch.exe")
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


def generate_release_notes(version):
    """git log에서 이전 태그 이후 커밋 메시지를 추출해서 릴리즈 노트 생성."""
    notes = []
    notes.append(f"# {version} 한국어 번역패치")
    notes.append(f"")
    notes.append(f"릴리즈 날짜: {datetime.now().strftime('%Y-%m-%d')}")
    notes.append(f"")

    # git log에서 커밋 메시지 추출
    try:
        # 이전 태그 찾기
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD~1"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        prev_tag = result.stdout.strip() if result.returncode == 0 else ""

        if prev_tag:
            log_cmd = ["git", "log", f"{prev_tag}..HEAD", "--pretty=format:%s"]
        else:
            log_cmd = ["git", "log", "--oneline", "-20", "--pretty=format:%s"]

        result = subprocess.run(
            log_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )

        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")

            # 커밋 메시지 분류
            translations = []
            fixes = []
            features = []
            updates = []
            others = []

            for msg in commits:
                msg = msg.strip()
                if not msg:
                    continue
                lower = msg.lower()
                if lower.startswith("fix:") or lower.startswith("fix ") or "수정" in msg or "오류" in msg:
                    fixes.append(msg.split(":", 1)[-1].strip() if ":" in msg else msg)
                elif lower.startswith("feat:") or lower.startswith("feat ") or "추가" in msg:
                    features.append(msg.split(":", 1)[-1].strip() if ":" in msg else msg)
                elif lower.startswith("update:") or lower.startswith("update ") or "업데이트" in msg or "대응" in msg:
                    updates.append(msg.split(":", 1)[-1].strip() if ":" in msg else msg)
                elif "번역" in msg or "조사" in msg or "용어" in msg or "오역" in msg:
                    translations.append(msg)
                else:
                    others.append(msg)

            if updates:
                notes.append("## 🎮 게임 업데이트 대응")
                for m in updates:
                    notes.append(f"- {m}")
                notes.append("")

            if translations:
                notes.append("## 🔤 번역 수정")
                for m in translations:
                    notes.append(f"- {m}")
                notes.append("")

            if fixes:
                notes.append("## 🐛 버그 수정")
                for m in fixes:
                    notes.append(f"- {m}")
                notes.append("")

            if features:
                notes.append("## ✨ 새 기능")
                for m in features:
                    notes.append(f"- {m}")
                notes.append("")

            if others:
                notes.append("## 📝 기타")
                for m in others:
                    notes.append(f"- {m}")
                notes.append("")

    except Exception as e:
        notes.append(f"(커밋 로그 추출 실패: {e})")
        notes.append("")

    # JSON 파일 수 정보
    ko_fix = Path("ko_fix_origin")
    if ko_fix.exists():
        json_count = len(list(ko_fix.glob("*.json")))
        notes.append("## 📦 패치 정보")
        notes.append(f"- 번역 파일: {json_count}개")
        notes.append(f"- LELocalePatch.exe 포함")
        notes.append("")

    notes.append("## 🚀 사용법")
    notes.append("1. `LastEpoch_KR_Patcher.exe` 다운로드")
    notes.append("2. 실행 (Steam 경로 자동 감지)")
    notes.append("3. 🚀 패치 적용 클릭")
    notes.append("")
    notes.append("## ↩ 복원")
    notes.append("- 패처에서 복원 버튼 또는 Steam → 파일 무결성 검사")
    notes.append("")
    notes.append("---")
    notes.append("※ 본 패치는 게임 번들 파일을 수정합니다. 사용으로 인한 문제는 사용자 본인 책임입니다.")

    return "\n".join(notes)


def download_patcher_exe(output_dir):
    """GitHub 릴리즈에서 최신 패처 exe 다운로드."""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
    req = urllib.request.Request(api_url, headers={
        "User-Agent": "LETransKr-Release",
        "Accept": "application/vnd.github.v3+json"
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
            releases = json.loads(resp.read().decode("utf-8"))

        for rel in releases:
            for asset in rel.get("assets", []):
                name = asset["name"]
                if "patcher" in name.lower() and name.lower().endswith(".exe"):
                    exe_path = os.path.join(output_dir, name)
                    print(f"  패처 exe 다운로드: {name} (from {rel['tag_name']})")
                    dl_req = urllib.request.Request(asset["browser_download_url"],
                                                    headers={"User-Agent": "LETransKr-Release"})
                    with urllib.request.urlopen(dl_req, timeout=120, context=SSL_CONTEXT) as dl_resp:
                        with open(exe_path, "wb") as f:
                            f.write(dl_resp.read())
                    size_mb = os.path.getsize(exe_path) / 1024 / 1024
                    print(f"  ✅ 다운로드 완료: {name} ({size_mb:.1f}MB)")
                    return exe_path

        print("  ⚠️ 릴리즈에서 패처 exe를 찾을 수 없습니다")
    except Exception as e:
        print(f"  ⚠️ 패처 exe 다운로드 실패: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description="검증 후 릴리즈 에셋 준비")
    parser.add_argument("json_folder", type=Path)
    parser.add_argument("lelocale_exe", type=Path)
    parser.add_argument("--version", "-v", required=True)
    parser.add_argument("--output-dir", "-o", type=Path, default=Path(__file__).parent / 'release')
    parser.add_argument("--en-dir", type=Path, default=Path(__file__).parent / 'en_dump')
    parser.add_argument("--baseline", type=Path, help="검토된 기존 스냅샷의 동일 오류만 구분")
    parser.add_argument("--include-patcher", action="store_true", help="기존 릴리즈 패처 exe도 포함")
    parser.add_argument("--font-tool", type=Path, help="LEFontPatch.exe 경로 (패처의 폰트 교체 기능용 에셋)")
    args = parser.parse_args()
    if args.font_tool and (not args.font_tool.is_file() or args.font_tool.name != 'LEFontPatch.exe'):
        parser.error('--font-tool은 LEFontPatch.exe 파일이어야 함')
    if not re.fullmatch(r'v?[0-9]+(?:\.[0-9]+){1,3}(?:-[A-Za-z0-9.-]+)?', args.version):
        parser.error('잘못된 버전 태그')
    issues = validate_dataset(args.en_dir, args.json_folder)
    errors, known = split_known_issues(issues, args.baseline)
    if errors:
        for item in errors[:20]:
            print(f"{item['file']}:{item['key']} {item['error']}")
        raise SystemExit(f'배포 중단: 새 구조 오류 {len(errors)}건. verify.py --strict로 확인')
    out = args.output_dir.resolve() / args.version
    if out.exists():
        raise SystemExit(f'기존 배포 폴더 보존: {out}. 다른 버전/출력 경로 사용')
    out.parent.mkdir(parents=True, exist_ok=True)
    # Freeze JSON inputs after validation and revalidate this exact package input.
    with tempfile.TemporaryDirectory(prefix='.release-', dir=out.parent) as tmp:
        stage = Path(tmp) / 'assets'; stage.mkdir()
        frozen = Path(tmp) / 'json'; frozen.mkdir()
        for path in locale_files(args.json_folder):
            shutil.copy2(path, frozen / path.name)
        frozen_errors, frozen_known = split_known_issues(validate_dataset(args.en_dir, frozen), args.baseline)
        if frozen_errors:
            raise SystemExit('검증 중 번역 변경 감지. 다시 실행 필요')
        zip_path = stage / f'kr-patch-{args.version}.zip'
        create_patch_bundle(frozen, args.lelocale_exe, zip_path, args.version)
        files = [str(zip_path)]
        if args.include_patcher:
            exe = download_patcher_exe(str(stage))
            if not exe:
                raise SystemExit('요청한 패처 exe 다운로드 실패')
            files.append(exe)
        if args.font_tool:
            # 패처는 SHA256SUMS에 이 이름으로 적힌 해시가 있어야 내려받음
            shutil.copy2(args.font_tool, stage / args.font_tool.name)
            files.append(str(stage / args.font_tool.name))
        try:
            commit = subprocess.run(['git', '-C', str(Path(__file__).parent), 'rev-parse', 'HEAD'],
                                    capture_output=True, text=True)
            commit = commit.stdout.strip() if commit.returncode == 0 else None
        except FileNotFoundError:  # git not installed
            commit = None
        manifest = {'version': args.version, 'commit': commit,
                    'known_structural_issues': len(frozen_known),
                    'files': {p.name: sha256_file(p) for p in locale_files(frozen)},
                    'lelocale_sha256': sha256_file(args.lelocale_exe)}
        # Manifest stays outside ZIP: upstream imports table JSONs only.
        write_json(stage / 'release_manifest.json', manifest)
        files.append(str(stage / 'release_manifest.json'))
        (stage / 'RELEASE_NOTES.md').write_text(generate_release_notes(args.version), encoding='utf-8')
        files.append(str(stage / 'RELEASE_NOTES.md'))
        create_checksums(files, stage / 'SHA256SUMS')
        stage.rename(out)
    print(f'배포 준비 완료: {out} / 기존 기준 항목 {len(known)}건 / 새 구조 오류 0건')
    print('GitHub 업로드는 별도. 게임 내 문맥 검수 완료를 의미하지 않음.')


if __name__ == '__main__':
    main()
