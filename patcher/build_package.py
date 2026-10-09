"""
패처 배포 패키지 생성 (PyInstaller 미사용)

python.org가 배포하는 Windows용 Python을 받아 해시를 확인하고, 패처 소스(.py)를 그대로 옆에 둔 폴더를 만듦.
실행 파일(exe/dll/pyd)은 모두 Python Software Foundation·Microsoft 서명본이고 직접 만든 실행 파일은 없음.

    python build_package.py            # dist/LastEpoch_KR_Patcher/ 와 zip 생성
    python build_package.py --out 폴더
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PYTHON_VERSION = "3.14.7"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.zip"
PYTHON_SHA256 = "ac1a727a71738e11de80b76e975f9b8a258aea6412bfc31696b929d59c6aafd0"
PYTHON_TAG = "python" + "".join(PYTHON_VERSION.split(".")[:2])

# 루트 인증서 묶음 (Mozilla 목록, certifi 배포본에서 cacert.pem만 꺼냄)
CERTIFI_URL = ("https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/"
               "certifi-2026.7.22-py3-none-any.whl")
CERTIFI_SHA256 = "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775"
CA_BUNDLE = "cacert.pem"

PACKAGE_NAME = "LastEpoch_KR_Patcher"
APP_FILES = ["patcher.py", "locale_runner.py", "workbench_common.py", "unity_bundle.py", "icon.ico",
             "Maplestory Bold.ttf", "Maplestory-LICENSE.txt"]

# 패처가 쓰지 않는 런타임 구성요소
PRUNE_ROOT = ["Doc", "include", "libs", "Scripts", "__install__.json"]
PRUNE_LIB = ["test", "idlelib", "turtledemo", "ensurepip", "site-packages", "venv", "pydoc_data", "__phello__"]
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

LAUNCHER = r"""@echo off
if not exist "%~dp0runtime\pythonw.exe" goto missing
if not "%~1"=="" goto cli
start "" "%~dp0runtime\pythonw.exe" -B "%~dp0app\patcher.py"
exit /b
:cli
"%~dp0runtime\python.exe" -B "%~dp0app\patcher.py" %*
exit /b %errorlevel%
:missing
chcp 65001 >nul
echo 압축을 먼저 풀고, 푼 폴더 안에서 실행해주세요.
pause
"""

README = """Last Epoch 한국어 번역패치 패처 {version}

실행: LastEpoch_KR_Patcher.cmd 더블클릭 (압축을 푼 폴더 안에서)

구성
  LastEpoch_KR_Patcher.cmd  실행용 배치 파일 (메모장으로 열어볼 수 있음)
  app\\                      패처 소스 (.py, 메모장으로 열어볼 수 있음)와 루트 인증서 목록 (cacert.pem, Mozilla),
                            패처 창 글꼴 (메이플스토리 서체, 저작권 안내는 Maplestory-LICENSE.txt)
  runtime\\                  Python {python} 공식 배포본 (python.org, Python Software Foundation 서명)

직접 만든 실행 파일(exe)은 들어 있지 않습니다.
소스와 빌드 기록: https://github.com/fnrkp089/LETrans_Kr
"""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def patcher_version():
    match = re.search(r'^PATCHER_VERSION = "([^"]+)"', (HERE / "patcher.py").read_text(encoding="utf-8"), re.M)
    if not match:
        raise RuntimeError("patcher.py에서 PATCHER_VERSION을 찾을 수 없음")
    return match.group(1)


def fetch(cache, url, sha256):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / Path(url).name
    if not (archive.is_file() and sha256_file(archive) == sha256):
        print(f"다운로드: {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, open(archive, "wb") as f:
            shutil.copyfileobj(resp, f)
        if sha256_file(archive) != sha256:
            archive.unlink()
            raise RuntimeError(f"해시 불일치: {archive.name}")
    return archive


def write_zip(target, root, files):
    """경로순·고정 시각으로 기록 (같은 입력이면 같은 zip)"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())


def build_runtime(archive, runtime):
    with zipfile.ZipFile(archive) as z:
        z.extractall(runtime)
    for name in PRUNE_ROOT:
        target = runtime / name
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    lib = runtime / "Lib"
    for name in PRUNE_LIB:
        shutil.rmtree(lib / name, ignore_errors=True)
    for cache in list(lib.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)

    # 표준 라이브러리는 미리 컴파일해 zip 하나로 (python.org embeddable 배포본과 같은 구성)
    subprocess.run([str(runtime / "python.exe"), "-I", "-m", "compileall", "-b", "-q",
                    "--invalidation-mode", "unchecked-hash", str(lib)], check=True)
    write_zip(runtime / f"{PYTHON_TAG}.zip", lib,
              [p for p in lib.rglob("*") if p.is_file() and p.suffix != ".py"])
    shutil.rmtree(lib)
    # ._pth: 사용자 PC의 PYTHONPATH·레지스트리·site-packages와 분리
    (runtime / f"{PYTHON_TAG}._pth").write_text(f"{PYTHON_TAG}.zip\nDLLs\n..\\app\n", encoding="ascii", newline="\r\n")


def verify_signatures(package):
    """exe/dll/pyd가 전부 유효한 서명본인지 확인. 하나라도 아니면 빌드 실패"""
    # -Include는 PowerShell 버전에 따라 -LiteralPath에서 무시되므로 확장자로 직접 거름
    script = ("$bin = @(Get-ChildItem -LiteralPath $args[0] -Recurse -File | "
              "Where-Object { '.exe','.dll','.pyd' -contains $_.Extension.ToLower() }); "
              "if ($bin.Count -lt 10) { 'binaries: ' + $bin.Count; exit 1 }; "
              "$bad = @($bin | Where-Object { (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne 'Valid' }); "
              "$bad | ForEach-Object { $_.FullName }; if ($bad.Count) { exit 1 }")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", f"& {{ {script} }}", str(package)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"서명 없는 실행 파일: {result.stdout.strip() or result.stderr.strip()}")


def build(out):
    if sys.platform != "win32":
        raise RuntimeError("Windows에서만 빌드 가능")
    version = patcher_version()
    package = out / PACKAGE_NAME
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    build_runtime(fetch(out / "cache", PYTHON_URL, PYTHON_SHA256), package / "runtime")
    (package / "app").mkdir()
    for name in APP_FILES:
        shutil.copyfile(HERE / name, package / "app" / name)
    with zipfile.ZipFile(fetch(out / "cache", CERTIFI_URL, CERTIFI_SHA256)) as z:
        (package / "app" / CA_BUNDLE).write_bytes(z.read(f"certifi/{CA_BUNDLE}"))
    # 패처 자체 업데이트가 읽음: python이 같을 때만 app 폴더만 교체
    (package / "app" / "package.json").write_text(
        json.dumps({"patcher": version, "python": PYTHON_VERSION}, indent=2) + "\n", encoding="utf-8", newline="\n")
    # cmd는 UTF-8(BOM 없음)·CRLF. 한글은 chcp 65001 뒤에서만 출력
    (package / f"{PACKAGE_NAME}.cmd").write_text(LAUNCHER, encoding="utf-8", newline="\r\n")
    (package / "README.txt").write_text(README.format(version=version, python=PYTHON_VERSION),
                                        encoding="utf-8-sig", newline="\r\n")

    verify_signatures(package)

    target = out / f"{PACKAGE_NAME}-v{version}.zip"
    write_zip(target, out, [p for p in package.rglob("*") if p.is_file()])
    # 자체 업데이트용: app 폴더만 (기존 사용자는 이것만 받아 교체)
    app_zip = out / f"{PACKAGE_NAME}-app-v{version}.zip"
    write_zip(app_zip, package / "app", [p for p in (package / "app").iterdir() if p.is_file()])
    sums = "".join(f"{sha256_file(p)}  {p.name}\n" for p in (target, app_zip))
    (out / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")
    print(f"{target}  {target.stat().st_size / 1e6:.1f} MB\n{app_zip}  {app_zip.stat().st_size / 1e3:.0f} KB\n{sums}", end="")
    return target


def main():
    parser = argparse.ArgumentParser(description="패처 배포 패키지 생성")
    parser.add_argument("--out", default=str(HERE / "dist"), help="출력 폴더 (기본: dist)")
    build(Path(parser.parse_args().out).resolve())


if __name__ == "__main__":
    main()
