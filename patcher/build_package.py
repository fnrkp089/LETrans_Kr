"""
패처 배포 패키지 생성 (PyInstaller 미사용)

python.org가 배포하는 Windows용 Python을 받아 해시를 확인하고, 패처 소스(.py)를 그대로 옆에 둔 폴더를 만듦.
실행 파일(exe/dll/pyd)은 모두 Python Software Foundation·Microsoft 서명본이고 직접 만든 실행 파일은 없음.

    python build_package.py            # dist/LastEpoch_KR_Patcher/ 와 zip 생성
    python build_package.py --out 폴더
"""

import argparse
import hashlib
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

PACKAGE_NAME = "LastEpoch_KR_Patcher"
APP_FILES = ["patcher.py", "locale_runner.py", "workbench_common.py"]

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
  app\\                      패처 소스 (.py, 메모장으로 열어볼 수 있음)
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


def fetch_python(cache):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / Path(PYTHON_URL).name
    if not (archive.is_file() and sha256_file(archive) == PYTHON_SHA256):
        print(f"다운로드: {PYTHON_URL}")
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as resp, open(archive, "wb") as f:
            shutil.copyfileobj(resp, f)
        if sha256_file(archive) != PYTHON_SHA256:
            archive.unlink()
            raise RuntimeError("Python 배포본 해시 불일치")
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
    script = ("$bad = Get-ChildItem -LiteralPath $args[0] -Recurse -File -Include *.exe,*.dll,*.pyd | "
              "Where-Object { (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne 'Valid' }; "
              "$bad | ForEach-Object { $_.FullName }; if ($bad) { exit 1 }")
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

    build_runtime(fetch_python(out / "cache"), package / "runtime")
    (package / "app").mkdir()
    for name in APP_FILES:
        shutil.copyfile(HERE / name, package / "app" / name)
    # cmd는 UTF-8(BOM 없음)·CRLF. 한글은 chcp 65001 뒤에서만 출력
    (package / f"{PACKAGE_NAME}.cmd").write_text(LAUNCHER, encoding="utf-8", newline="\r\n")
    (package / "README.txt").write_text(README.format(version=version, python=PYTHON_VERSION),
                                        encoding="utf-8-sig", newline="\r\n")

    verify_signatures(package)

    target = out / f"{PACKAGE_NAME}-v{version}.zip"
    write_zip(target, out, [p for p in package.rglob("*") if p.is_file()])
    print(f"{target}  {target.stat().st_size / 1e6:.1f} MB\nSHA256 {sha256_file(target)}")
    return target


def main():
    parser = argparse.ArgumentParser(description="패처 배포 패키지 생성")
    parser.add_argument("--out", default=str(HERE / "dist"), help="출력 폴더 (기본: dist)")
    build(Path(parser.parse_args().out).resolve())


if __name__ == "__main__":
    main()
