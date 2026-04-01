"""
Last Epoch 한국어 번역패치 원클릭 적용기
GitHub: fnrkp089/LETrans_Kr
"""

import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import logging
import tempfile
import subprocess
import threading
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

try:
    import winreg
except ImportError:
    winreg = None

try:
    import detools
    HAS_DETOOLS = True
except ImportError:
    HAS_DETOOLS = False

# ─── 상수 ────────────────────────────────────────────────
GITHUB_REPO = "fnrkp089/LETrans_Kr"
STEAM_APP_ID = "899770"
GAME_FOLDER_NAME = "Last Epoch"
PATCHER_VERSION = "0.2.5"

GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_API_LATEST = f"{GITHUB_API_RELEASES}/latest"
USER_AGENT = f"LastEpoch-KR-Patcher/{PATCHER_VERSION}"

BUNDLE_SUBDIR = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "StandaloneWindows64"
BUNDLE_FILENAME = "localization-string-tables-korean(ko)_assets_all.bundle"
CATALOG_RELPATH = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "catalog.json"

PATCH_STATE_FILE = "kr_patch_state.json"
BACKUP_DIR_NAME = "kr_patch_backup"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("patcher")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  유틸: 시맨틱 버전 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def parse_version(ver: str) -> tuple[int, ...]:
    """
    "v0.1.0" → (0, 1, 0)
    "patcher-v1.2.3" → (1, 2, 3)
    """
    cleaned = re.sub(r"^[a-zA-Z-]*v?", "", ver.strip())
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. STEAM 경로 탐지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def find_steam_install_path() -> str | None:
    if winreg is None:
        return None
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
    ]
    for hive, subkey in reg_paths:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                val, _ = winreg.QueryValueEx(key, "InstallPath")
                if val and Path(val).exists():
                    return str(val)
        except (FileNotFoundError, OSError):
            continue
    return None


def parse_vdf_library_folders(steam_path: str) -> list[str]:
    vdf_candidates = [
        Path(steam_path) / "steamapps" / "libraryfolders.vdf",
        Path(steam_path) / "config" / "libraryfolders.vdf",
    ]
    folders = [steam_path]
    for vdf_path in vdf_candidates:
        if not vdf_path.exists():
            continue
        try:
            content = vdf_path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r'"path"\s+"([^"]+)"', content):
                p = match.group(1).replace("\\\\", "\\")
                if Path(p).exists() and p not in folders:
                    folders.append(p)
        except Exception:
            pass
        break
    return folders


def read_acf_value(acf_path: Path, key: str) -> str | None:
    try:
        content = acf_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(rf'"{key}"\s+"([^"]+)"', content)
        return m.group(1) if m else None
    except Exception:
        return None


def find_game_path() -> str | None:
    steam_path = find_steam_install_path()
    if not steam_path:
        return None
    for lib in parse_vdf_library_folders(steam_path):
        steamapps = Path(lib) / "steamapps"
        manifest = steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
        if not manifest.exists():
            continue
        installdir = read_acf_value(manifest, "installdir")
        if installdir:
            game_dir = steamapps / "common" / installdir
            if game_dir.exists():
                return str(game_dir)
        game_dir = steamapps / "common" / GAME_FOLDER_NAME
        if game_dir.exists():
            return str(game_dir)
    return None


def get_steam_buildid(game_path: str) -> str | None:
    game_dir = Path(game_path)
    steamapps = game_dir.parent.parent
    manifest = steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
    if manifest.exists():
        return read_acf_value(manifest, "buildid")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. GitHub API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def github_api_get(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github.v3+json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest_release() -> dict:
    return github_api_get(GITHUB_API_LATEST)


def fetch_patcher_latest_version() -> tuple[str, str] | None:
    """패처 exe가 포함된 릴리즈에서 버전과 URL을 반환."""
    try:
        releases = github_api_get(GITHUB_API_RELEASES)
        for rel in releases:
            for asset in rel.get("assets", []):
                name = asset["name"].lower()
                if "patcher" in name and name.endswith(".exe"):
                    return (rel["tag_name"], asset["browser_download_url"])
    except Exception:
        pass
    return None


def find_release_assets(release: dict) -> dict[str, dict]:
    assets = {}
    for a in release.get("assets", []):
        name = a["name"].lower()
        info = {
            "name": a["name"],
            "url": a["browser_download_url"],
            "size": a.get("size", 0),
        }
        if "sha256" in name or "checksum" in name:
            assets["checksums"] = info
        elif "delta" in name and name.endswith(".patch"):
            assets["delta_patch"] = info
        elif name.endswith(".zip"):
            assets["patch_bundle"] = info
    return assets


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 다운로드 + 무결성 검증
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def download_file(url: str, dest: str, progress_cb=None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def sha256_file(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(filepath: str, expected_hash: str) -> bool:
    return sha256_file(filepath).lower() == expected_hash.lower()


def download_and_parse_checksums(url: str) -> dict[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        text = resp.read().decode("utf-8")
    result = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result[parts[-1].lstrip("*")] = parts[0]
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 패치 상태 관리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PatchState:
    def __init__(self, game_path: str):
        self.filepath = Path(game_path) / PATCH_STATE_FILE
        self.data = self._load()

    def _load(self) -> dict:
        if self.filepath.exists():
            try:
                return json.loads(self.filepath.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def save(self):
        self.filepath.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @property
    def patch_version(self) -> str | None:
        return self.data.get("patch_version")

    @property
    def game_buildid(self) -> str | None:
        return self.data.get("game_buildid")

    def is_outdated(self, new_version: str) -> bool:
        current = self.patch_version
        if not current:
            return True
        return parse_version(current) < parse_version(new_version)

    def game_was_updated(self, current_buildid: str) -> bool:
        saved = self.game_buildid
        if not saved:
            return False
        return saved != current_buildid

    def update(self, patch_version: str, game_buildid: str | None,
               bundle_hash: str, files_applied: list[str]):
        self.data.update({
            "patch_version": patch_version,
            "patch_date": datetime.now().isoformat(),
            "game_buildid": game_buildid,
            "bundle_hash": bundle_hash,
            "patcher_version": PATCHER_VERSION,
            "files_applied": files_applied,
        })
        self.save()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. 백업 / 복원
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def create_backup(game_path: str) -> str | None:
    game = Path(game_path)
    backup_dir = game / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)

    backed_up = []
    bundle = game / BUNDLE_SUBDIR / BUNDLE_FILENAME
    if bundle.exists():
        shutil.copy2(bundle, backup_dir / BUNDLE_FILENAME)
        backed_up.append(BUNDLE_FILENAME)

    catalog = game / CATALOG_RELPATH
    if catalog.exists():
        shutil.copy2(catalog, backup_dir / "catalog.json")
        backed_up.append("catalog.json")

    if backed_up:
        log.info(f"백업 완료: {backed_up}")
        return str(backup_dir)
    return None


def restore_backup(game_path: str) -> bool:
    game = Path(game_path)
    backup_dir = game / BACKUP_DIR_NAME
    if not backup_dir.exists():
        return False

    restored = []
    bk_bundle = backup_dir / BUNDLE_FILENAME
    if bk_bundle.exists():
        shutil.copy2(bk_bundle, game / BUNDLE_SUBDIR / BUNDLE_FILENAME)
        restored.append(BUNDLE_FILENAME)

    bk_catalog = backup_dir / "catalog.json"
    if bk_catalog.exists():
        shutil.copy2(bk_catalog, game / CATALOG_RELPATH)
        restored.append("catalog.json")

    if restored:
        state_file = game / PATCH_STATE_FILE
        if state_file.exists():
            state_file.unlink()
        log.info(f"복원 완료: {restored}")
        return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. LELocalePatch CLI 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def find_bundle_path(game_path: str) -> Path | None:
    bundle = Path(game_path) / BUNDLE_SUBDIR / BUNDLE_FILENAME
    if bundle.exists():
        return bundle
    bundle_dir = Path(game_path) / BUNDLE_SUBDIR
    if bundle_dir.exists():
        for f in bundle_dir.glob("*korean*"):
            if f.suffix == ".bundle":
                return f
        for f in bundle_dir.glob("*ko*"):
            if f.suffix == ".bundle" and "localization" in f.name.lower():
                return f
    return None


def run_lelocale_patch(lelocale_exe, bundle_path, action, json_source, progress_cb=None):
    cmd = [lelocale_exe, bundle_path, action, json_source]
    log.info(f"LELocalePatch 실행: {' '.join(cmd)}")
    if progress_cb:
        progress_cb("LELocalePatch 실행 중...", -1)
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"LELocalePatch 실행 실패 (exit code: {result.returncode})\n"
            f"{result.stderr or result.stdout}"
        )
    log.info(f"LELocalePatch 완료:\n{result.stdout}")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. 델타 패칭
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def apply_delta_patch(original, delta, output) -> bool:
    if not HAS_DETOOLS:
        return False
    try:
        with open(original, "rb") as fo, \
             open(delta, "rb") as fd, \
             open(output, "wb") as fout:
            detools.apply_patch(fo, fd, fout)
        return True
    except Exception as e:
        log.error(f"델타 패치 실패: {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. 패처 자기 업데이트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_patcher_update() -> tuple[bool, str, str]:
    """Returns: (update_available, latest_version, download_url)."""
    try:
        result = fetch_patcher_latest_version()
        if result is None:
            return (False, PATCHER_VERSION, "")
        latest_tag, dl_url = result
        if parse_version(latest_tag) > parse_version(PATCHER_VERSION):
            return (True, latest_tag, dl_url)
    except Exception as e:
        log.warning(f"패처 업데이트 확인 실패: {e}")
    return (False, PATCHER_VERSION, "")


def self_update(download_url: str) -> bool:
    if not getattr(sys, "frozen", False):
        log.info("개발 모드 — 자기 업데이트 스킵")
        return False
    current_exe = sys.executable
    try:
        new_exe = current_exe + ".new"
        download_file(download_url, new_exe)
        bat_path = current_exe + ".update.bat"
        with open(bat_path, "w") as f:
            f.write(f"""@echo off
timeout /t 2 /nobreak >nul
del "{current_exe}"
move "{new_exe}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
        subprocess.Popen(["cmd", "/c", bat_path],
                         creationflags=subprocess.CREATE_NO_WINDOW)
        sys.exit(0)
    except Exception as e:
        log.error(f"자기 업데이트 실패: {e}")
        for tmp in [current_exe + ".new", current_exe + ".update.bat"]:
            if os.path.exists(tmp):
                os.remove(tmp)
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  9. 메인 오케스트레이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PatchOrchestrator:
    def __init__(self, game_path, log_cb=None, status_cb=None, progress_cb=None):
        self.game_path = game_path
        self.state = PatchState(game_path)
        self._log = log_cb or (lambda msg: log.info(msg))
        self._status = status_cb or (lambda msg: None)
        self._progress = progress_cb or (lambda val, total: None)

    def _dl_progress(self, downloaded, total):
        self._progress(downloaded, total)
        if total > 0:
            pct = downloaded / total * 100
            mb_dl = downloaded / 1024 / 1024
            mb_tot = total / 1024 / 1024
            self._status(f"다운로드 중... {mb_dl:.1f}MB / {mb_tot:.1f}MB ({pct:.0f}%)")

    def check_game_updated(self) -> bool:
        current = get_steam_buildid(self.game_path)
        if current and self.state.game_was_updated(current):
            self._log(
                f"⚠️ 게임 업데이트 감지! "
                f"(저장: {self.state.game_buildid} → 현재: {current})"
            )
            return True
        return False

    def run(self) -> dict:
        result = {"success": False, "version": "", "files": [], "message": ""}
        try:
            self._status("GitHub에서 최신 릴리즈 확인 중...")
            self._log("GitHub Releases API 호출...")
            release = fetch_latest_release()
            tag = release.get("tag_name", "unknown")
            self._log(f"최신 릴리즈: {tag}")

            game_updated = self.check_game_updated()
            if not self.state.is_outdated(tag) and not game_updated:
                msg = f"이미 최신 패치 적용됨 ({tag})"
                self._log(f"✅ {msg}")
                self._status(msg)
                result.update(success=True, version=tag, message=msg)
                return result

            if game_updated:
                self._log("게임 업데이트로 인해 패치를 재적용합니다.")

            assets = find_release_assets(release)
            if "patch_bundle" not in assets:
                raise RuntimeError(
                    f"릴리즈에서 패치 번들(.zip)을 찾을 수 없습니다.\n"
                    f"https://github.com/{GITHUB_REPO}/releases"
                )

            bundle_asset = assets["patch_bundle"]
            size_mb = bundle_asset["size"] / 1024 / 1024
            self._log(f"패치 번들: {bundle_asset['name']} ({size_mb:.1f}MB)")

            checksums = {}
            if "checksums" in assets:
                try:
                    checksums = download_and_parse_checksums(assets["checksums"]["url"])
                    self._log(f"체크섬 로드: {len(checksums)}개 파일")
                except Exception as e:
                    self._log(f"⚠️ 체크섬 다운로드 실패 (무시): {e}")

            bundle_path = find_bundle_path(self.game_path)
            if not bundle_path:
                raise RuntimeError(
                    "한국어 로컬라이제이션 번들을 찾을 수 없습니다.\n"
                    "게임에서 언어를 한국어로 한번 설정한 후 다시 시도해주세요.\n"
                    f"예상 경로: {Path(self.game_path) / BUNDLE_SUBDIR}"
                )
            self._log(f"번들: {bundle_path}")

            with tempfile.TemporaryDirectory() as tmpdir:
                use_delta = False
                if HAS_DETOOLS and "delta_patch" in assets and self.state.patch_version:
                    self._log("델타 패치 시도...")
                    delta_asset = assets["delta_patch"]
                    delta_path = os.path.join(tmpdir, delta_asset["name"])
                    download_file(delta_asset["url"], delta_path, self._dl_progress)
                    new_bundle = os.path.join(tmpdir, "patched_bundle")
                    use_delta = apply_delta_patch(str(bundle_path), delta_path, new_bundle)
                    if use_delta:
                        self._log("✅ 델타 패치 성공!")
                        shutil.copy2(new_bundle, str(bundle_path))

                if not use_delta:
                    self._status(f"패치 번들 다운로드 중... ({size_mb:.1f}MB)")
                    zip_path = os.path.join(tmpdir, bundle_asset["name"])
                    download_file(bundle_asset["url"], zip_path, self._dl_progress)
                    self._log("다운로드 완료!")

                    if bundle_asset["name"] in checksums:
                        self._status("무결성 검증 중...")
                        if not verify_checksum(zip_path, checksums[bundle_asset["name"]]):
                            raise RuntimeError("체크섬 불일치! 다시 시도해주세요.")
                        self._log("✅ SHA256 검증 통과")

                    self._status("패치 번들 압축 해제 중...")
                    extract_dir = os.path.join(tmpdir, "extracted")
                    os.makedirs(extract_dir)
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(extract_dir)

                    lelocale_exe = self._find_file(extract_dir, "lelocalepatch.exe")
                    json_source = self._find_json_source(extract_dir)

                    self._status("기존 파일 백업 중...")
                    bk = create_backup(self.game_path)
                    self._log(f"백업: {bk or '(없음)'}")

                    if lelocale_exe:
                        self._status("LELocalePatch로 번들 패치 중...")
                        run_lelocale_patch(lelocale_exe, str(bundle_path), "patch", json_source)
                        files = self._list_json_files(json_source)
                        self._log(f"LELocalePatch: {len(files)}개 JSON 적용")
                    else:
                        self._log("LELocalePatch.exe 미포함 — 직접 복사 모드")
                        files = self._apply_direct_copy(extract_dir)

                current_buildid = get_steam_buildid(self.game_path)
                bh = sha256_file(str(bundle_path))
                applied_files = files if not use_delta else ["(delta)"]
                self.state.update(tag, current_buildid, bh, applied_files)

                msg = f"패치 적용 완료! ({tag}, {len(applied_files)}개 파일)"
                result.update(success=True, version=tag, files=applied_files, message=msg)
                self._log(f"✅ {msg}")
                self._status(f"✅ {msg}")

        except Exception as e:
            result["message"] = str(e)
            self._log(f"❌ 오류: {e}")
            self._status("❌ 오류 발생")
            log.exception("Patch failed")
        return result

    def _find_file(self, root_dir, filename_lower):
        for root, dirs, files in os.walk(root_dir):
            for f in files:
                if f.lower() == filename_lower:
                    return os.path.join(root, f)
        return None

    def _find_json_source(self, extract_dir):
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                if f.endswith(".zip") and "json" in f.lower():
                    return os.path.join(root, f)
        for root, dirs, files in os.walk(extract_dir):
            if any(f.endswith(".json") for f in files):
                return root
        return extract_dir

    def _list_json_files(self, source):
        if os.path.isfile(source) and source.endswith(".zip"):
            with zipfile.ZipFile(source, "r") as zf:
                return [n for n in zf.namelist() if n.endswith(".json")]
        elif os.path.isdir(source):
            return [f for f in os.listdir(source) if f.endswith(".json")]
        return []

    def _apply_direct_copy(self, extract_dir):
        self._status("직접 복사 모드 적용 중...")
        applied = []
        dest_base = Path(self.game_path)
        for root, dirs, files in os.walk(extract_dir):
            for f in files:
                src = Path(root) / f
                rel = src.relative_to(extract_dir)
                parts = rel.parts
                rel = Path(*parts[1:]) if len(parts) > 1 else Path(parts[0])
                target = dest_base / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
                applied.append(str(rel))
        self._log(f"직접 복사 완료: {len(applied)}개 파일")
        return applied


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  10. GUI (tkinter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class PatcherApp:
        BG = "#0f0f1a"
        BG2 = "#161628"
        FG = "#e0e0e0"
        ACCENT = "#ff4d6a"
        GREEN = "#4cdf8b"
        WARN = "#ffa726"
        ENTRY_BG = "#1c1c3a"

        def __init__(self, root):
            self.root = root
            self.root.title(f"Last Epoch 한국어 번역패치 v{PATCHER_VERSION}")
            self.root.geometry("600x540")
            self.root.resizable(False, False)
            self.root.configure(bg=self.BG)
            self.game_path = tk.StringVar()
            self.status_text = tk.StringVar(value="대기 중...")
            self._build_ui()
            self._auto_detect()
            self._check_patcher_update_bg()

        def _build_ui(self):
            s = ttk.Style()
            s.theme_use("clam")
            s.configure("Title.TLabel", background=self.BG, foreground=self.ACCENT, font=("맑은 고딕", 16, "bold"))
            s.configure("Sub.TLabel", background=self.BG, foreground="#888", font=("맑은 고딕", 9))
            s.configure("Status.TLabel", background=self.BG, foreground="#aaa", font=("맑은 고딕", 9))
            s.configure("Info.TLabel", background=self.BG2, foreground=self.FG, font=("맑은 고딕", 9))
            s.configure("Warn.TLabel", background=self.BG2, foreground=self.WARN, font=("맑은 고딕", 9))
            s.configure("TProgressbar", troughcolor=self.ENTRY_BG, background=self.ACCENT, thickness=20)

            top = tk.Frame(self.root, bg=self.BG)
            top.pack(fill="x", padx=24, pady=(20, 5))
            ttk.Label(top, text="⚔  Last Epoch 한국어 번역패치", style="Title.TLabel").pack(anchor="w")
            ttk.Label(top, text=f"github.com/{GITHUB_REPO}  ·  v{PATCHER_VERSION}", style="Sub.TLabel").pack(anchor="w")

            fp = tk.Frame(self.root, bg=self.BG)
            fp.pack(fill="x", padx=24, pady=(15, 5))
            ttk.Label(fp, text="게임 경로", style="Sub.TLabel").pack(anchor="w")
            fe = tk.Frame(fp, bg=self.BG)
            fe.pack(fill="x", pady=(3, 0))
            self.entry_path = tk.Entry(fe, textvariable=self.game_path, font=("Consolas", 10),
                                       bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG, relief="flat", bd=5)
            self.entry_path.pack(side="left", fill="x", expand=True)
            ttk.Button(fe, text="찾기", command=self._browse).pack(side="right", padx=(5, 0))

            fi = tk.Frame(self.root, bg=self.BG2, bd=1, relief="solid")
            fi.pack(fill="x", padx=24, pady=(10, 5))
            self.lbl_patch = ttk.Label(fi, text="  📦 패치 상태: 확인 중...", style="Info.TLabel")
            self.lbl_patch.pack(anchor="w", padx=8, pady=6)
            self.lbl_game = ttk.Label(fi, text="", style="Info.TLabel")
            self.lbl_game.pack(anchor="w", padx=8, pady=(0, 6))

            fo = tk.Frame(self.root, bg=self.BG)
            fo.pack(fill="x", padx=24, pady=(10, 5))
            self.do_backup = tk.BooleanVar(value=True)
            self.do_force = tk.BooleanVar(value=False)
            for text, var in [("적용 전 기존 파일 백업", self.do_backup), ("강제 재적용 (같은 버전이어도)", self.do_force)]:
                tk.Checkbutton(fo, text=text, variable=var, bg=self.BG, fg=self.FG,
                               selectcolor=self.ENTRY_BG, activebackground=self.BG,
                               activeforeground=self.FG, font=("맑은 고딕", 9)).pack(anchor="w")

            fp2 = tk.Frame(self.root, bg=self.BG)
            fp2.pack(fill="x", padx=24, pady=(10, 5))
            self.progress = ttk.Progressbar(fp2, mode="determinate", style="TProgressbar")
            self.progress.pack(fill="x")
            ttk.Label(fp2, textvariable=self.status_text, style="Status.TLabel").pack(anchor="w", pady=(3, 0))

            fl = tk.Frame(self.root, bg=self.BG)
            fl.pack(fill="both", expand=True, padx=24, pady=(5, 10))
            self.log_text = tk.Text(fl, height=7, bg=self.ENTRY_BG, fg="#7a7a9a",
                                    font=("Consolas", 8), relief="flat", bd=5, state="disabled")
            self.log_text.pack(fill="both", expand=True)

            fb = tk.Frame(self.root, bg=self.BG)
            fb.pack(fill="x", padx=24, pady=(0, 20))
            self.btn_apply = ttk.Button(fb, text="🚀 패치 적용", command=self._start)
            self.btn_apply.pack(side="left", fill="x", expand=True, ipady=8)
            self.btn_restore = ttk.Button(fb, text="↩ 복원", command=self._restore)
            self.btn_restore.pack(side="right", padx=(10, 0), ipady=8)

        def _log(self, msg):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        def _status(self, msg):
            self.status_text.set(msg)
            self.root.update_idletasks()

        def _prog(self, cur, tot):
            if tot > 0:
                self.progress["value"] = cur / tot * 100
            self.root.update_idletasks()

        def _auto_detect(self):
            self._log("Steam 경로 자동 감지 중...")
            game = find_game_path()
            if game:
                self.game_path.set(game)
                self._log(f"✅ 감지: {game}")
                self._status("게임 경로 자동 감지 완료!")
                self._refresh_info(game)
            else:
                self._log("❌ 자동 감지 실패")
                self._status("게임 경로를 수동 지정해주세요.")

        def _refresh_info(self, gp):
            state = PatchState(gp)
            bid = get_steam_buildid(gp)
            if state.patch_version:
                date = state.data.get("patch_date", "")[:10]
                self.lbl_patch.configure(text=f"  📦 적용 패치: {state.patch_version}  ({date})", style="Info.TLabel")
                if bid and state.game_was_updated(bid):
                    self.lbl_game.configure(text=f"  ⚠️ 게임 업데이트 감지! 재적용 권장 (build {bid})", style="Warn.TLabel")
                else:
                    self.lbl_game.configure(text=f"  🎮 게임 빌드: {bid or '?'}")
            else:
                self.lbl_patch.configure(text="  📦 패치 미적용")
                self.lbl_game.configure(text=f"  🎮 게임 빌드: {bid or '?'}")

        def _check_patcher_update_bg(self):
            def check():
                avail, ver, url = check_patcher_update()
                if avail:
                    self.root.after(0, lambda: self._prompt_update(ver, url))
            threading.Thread(target=check, daemon=True).start()

        def _prompt_update(self, ver, url):
            if messagebox.askyesno("패처 업데이트",
                                   f"새 패처 버전: {ver}\n현재: v{PATCHER_VERSION}\n\n업데이트?"):
                self_update(url)

        def _browse(self):
            p = filedialog.askdirectory(title="Last Epoch 폴더")
            if p:
                self.game_path.set(p)
                self._refresh_info(p)

        def _start(self):
            gp = self.game_path.get().strip()
            if not gp or not Path(gp).exists():
                messagebox.showerror("오류", "유효한 게임 경로를 지정해주세요.")
                return
            self.btn_apply.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self.progress["value"] = 0
            threading.Thread(target=self._run_patch, args=(gp,), daemon=True).start()

        def _run_patch(self, gp):
            orch = PatchOrchestrator(
                gp,
                log_cb=lambda m: self.root.after(0, self._log, m),
                status_cb=lambda m: self.root.after(0, self._status, m),
                progress_cb=lambda c, t: self.root.after(0, self._prog, c, t),
            )
            if self.do_force.get():
                orch.state.data.pop("patch_version", None)
            res = orch.run()
            def done():
                self.btn_apply.configure(state="normal")
                self.btn_restore.configure(state="normal")
                self._refresh_info(gp)
                if res["success"]:
                    self.progress["value"] = 100
                    messagebox.showinfo("완료", res["message"])
                else:
                    messagebox.showerror("오류", res["message"])
            self.root.after(0, done)

        def _restore(self):
            gp = self.game_path.get().strip()
            if not gp:
                messagebox.showerror("오류", "경로를 지정해주세요.")
                return
            if not messagebox.askyesno("확인", "백업에서 복원하시겠습니까?"):
                return
            if restore_backup(gp):
                self._log("✅ 복원 완료")
                self._status("원본 파일 복원됨")
                self._refresh_info(gp)
                messagebox.showinfo("완료", "복원 완료!")
            else:
                messagebox.showerror("오류", "백업을 찾을 수 없습니다.")

    root = tk.Tk()
    PatcherApp(root)
    root.mainloop()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  11. CLI 모드
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Last Epoch 한국어 번역패치")
    parser.add_argument("--path", help="게임 폴더 경로")
    parser.add_argument("--force", action="store_true", help="강제 재적용")
    parser.add_argument("--restore", action="store_true", help="백업 복원")
    parser.add_argument("--status", action="store_true", help="현재 패치 상태")
    parser.add_argument("--self-update", action="store_true", help="패처 업데이트")
    args = parser.parse_args()

    print(f"\n{'=' * 55}")
    print(f"  Last Epoch 한국어 번역패치 v{PATCHER_VERSION}")
    print(f"  github.com/{GITHUB_REPO}")
    print(f"{'=' * 55}\n")

    if args.self_update:
        avail, ver, url = check_patcher_update()
        if avail:
            print(f"새 버전: {ver}")
            if input("업데이트? (Y/n): ").strip().lower() != "n":
                self_update(url)
        else:
            print("최신 버전입니다.")
        return

    gp = args.path
    if not gp:
        print("[*] Steam 경로 탐색 중...")
        gp = find_game_path()
        if gp:
            print(f"  ✅ {gp}")
            if input("  맞습니까? (Y/n): ").strip().lower() == "n":
                gp = input("  경로: ").strip()
        else:
            gp = input("  ❌ 자동 감지 실패. 경로 입력: ").strip()

    if not gp or not Path(gp).exists():
        print("오류: 유효하지 않은 경로")
        sys.exit(1)

    if args.status:
        st = PatchState(gp)
        bid = get_steam_buildid(gp)
        print(f"  빌드: {bid or '?'}")
        if st.patch_version:
            print(f"  패치: {st.patch_version} ({st.data.get('patch_date', '?')[:10]})")
            print(f"  파일: {len(st.data.get('files_applied', []))}개")
            if bid and st.game_was_updated(bid):
                print("  ⚠️ 게임 업데이트 감지 — 재적용 권장!")
        else:
            print("  패치 미적용")
        return

    if args.restore:
        print("✅ 복원 완료!" if restore_backup(gp) else "❌ 백업 없음")
        return

    def cli_prog(dl, tot):
        if tot > 0:
            pct = dl / tot * 100
            bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
            print(f"\r  [{bar}] {pct:.0f}%", end="", flush=True)

    orch = PatchOrchestrator(gp,
        log_cb=lambda m: print(f"  {m}"),
        status_cb=lambda m: print(f"\n  >> {m}"),
        progress_cb=cli_prog)
    if args.force:
        orch.state.data.pop("patch_version", None)

    res = orch.run()
    print()
    if res["success"]:
        print(f"\n{'=' * 55}")
        print(f"  ✅ {res['message']}")
        print(f"  게임 실행해서 즐기세요!")
        print(f"{'=' * 55}")
    else:
        print(f"\n  ❌ {res['message']}")
        sys.exit(1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    if "--cli" in sys.argv or any(a.startswith("--") and a != "--cli" for a in sys.argv[1:]):
        run_cli()
    else:
        try:
            import tkinter
            run_gui()
        except ImportError:
            print("tkinter 없음 — CLI 모드\n")
            run_cli()

if __name__ == "__main__":
    main()
