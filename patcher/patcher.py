"""
Last Epoch 한국어 번역패치 원클릭 적용기
GitHub: fnrkp089/LETrans_Kr
"""

import os
from workbench_common import write_json, atomic_write, workspace_lock
from locale_runner import import_locale, run_checked
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
import ssl
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# SSL 인증서 설정 (PyInstaller exe에서 인증서 못 찾는 문제 해결)
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        SSL_CONTEXT = ssl.create_default_context()
    except Exception:
        raise RuntimeError("TLS 인증서 초기화 실패. 인증서 설정 확인 필요")

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
PATCHER_VERSION = "0.6.1"

GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_API_LATEST = f"{GITHUB_API_RELEASES}/latest"
USER_AGENT = f"LastEpoch-KR-Patcher/{PATCHER_VERSION}"

BUNDLE_SUBDIR = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "StandaloneWindows64"
BUNDLE_FILENAME = "localization-string-tables-korean(ko)_assets_all.bundle"
CATALOG_RELPATH = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "catalog.bin"

PATCH_STATE_FILE = "kr_patch_state.json"
BACKUP_DIR_NAME = "kr_patch_backup"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("patcher")


def parse_version(ver):
    cleaned = re.sub(r"^[a-zA-Z-]*v?", "", ver.strip())
    parts = []
    for p in cleaned.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


# ━━━ 1. STEAM 경로 탐지 ━━━

def find_steam_install_path():
    if winreg is None:
        return None
    for hive, subkey in [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
    ]:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                val, _ = winreg.QueryValueEx(key, "InstallPath")
                if val and Path(val).exists():
                    return str(val)
        except (FileNotFoundError, OSError):
            continue
    return None


def parse_vdf_library_folders(steam_path):
    folders = [steam_path]
    for vdf_path in [Path(steam_path) / "steamapps" / "libraryfolders.vdf", Path(steam_path) / "config" / "libraryfolders.vdf"]:
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


def read_acf_value(acf_path, key):
    try:
        content = acf_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(rf'"{key}"\s+"([^"]+)"', content)
        return m.group(1) if m else None
    except Exception:
        return None


def find_game_path():
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
    return None


def get_steam_buildid(game_path):
    steamapps = Path(game_path).parent.parent
    manifest = steamapps / f"appmanifest_{STEAM_APP_ID}.acf"
    return read_acf_value(manifest, "buildid") if manifest.exists() else None


# ━━━ 2. GitHub API ━━━

def github_api_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github.v3+json"})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_latest_release():
    return github_api_get(GITHUB_API_LATEST)

def find_release_assets(release):
    assets = {}
    for a in release.get("assets", []):
        name = a["name"].lower()
        info = {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
        if "sha256" in name or "checksum" in name:
            assets["checksums"] = info
        elif "delta" in name and name.endswith(".patch"):
            assets["delta_patch"] = info
        elif name.startswith("kr-patch-") and name.endswith(".zip"):
            assets["patch_bundle"] = info
    return assets


# ━━━ 3. 다운로드 + 검증 ━━━

def download_file(url, dest, progress_cb=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
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

def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def verify_checksum(filepath, expected_hash):
    return sha256_file(filepath).lower() == expected_hash.lower()

def download_and_parse_checksums(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
        text = resp.read().decode("utf-8")
    result = {}
    for line in text.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result[parts[-1].lstrip("*")] = parts[0]
    return result


# ━━━ 4. 패치 상태 관리 ━━━

class PatchState:
    def __init__(self, game_path):
        self.filepath = Path(game_path) / PATCH_STATE_FILE
        self.data = self._load()

    def _load(self):
        if self.filepath.exists():
            try:
                return json.loads(self.filepath.read_text("utf-8"))
            except Exception:
                pass
        return {}

    def save(self):
        write_json(self.filepath, self.data)

    @property
    def patch_version(self):
        return self.data.get("patch_version")

    @property
    def game_buildid(self):
        return self.data.get("game_buildid")

    def is_outdated(self, new_version):
        current = self.patch_version
        if not current:
            return True
        return parse_version(current) < parse_version(new_version)

    def game_was_updated(self, current_buildid):
        saved = self.game_buildid
        return saved != current_buildid if saved else False

    def update(self, patch_version, game_buildid, bundle_hash, files_applied):
        self.data.update({"patch_version": patch_version, "patch_date": datetime.now().isoformat(), "game_buildid": game_buildid, "bundle_hash": bundle_hash, "patcher_version": PATCHER_VERSION, "files_applied": files_applied})
        self.save()


# ━━━ 5. 백업 / 복원 ━━━

def _backup_files(game, backup_dir, metadata):
    names = metadata.get('files') or {
        BUNDLE_FILENAME: {'path': str(Path(BUNDLE_SUBDIR) / BUNDLE_FILENAME)},
        'catalog.bin': {'path': str(CATALOG_RELPATH)},
    }
    pairs = []
    for name, record in names.items():
        saved = (backup_dir / name).resolve()
        saved.relative_to(backup_dir.resolve())
        target = (game / record['path']).resolve()
        target.relative_to(game.resolve())
        if not saved.is_file():
            raise RuntimeError(f'불완전한 백업: {name}')
        if record.get('sha256') and sha256_file(saved) != record['sha256']:
            raise RuntimeError(f'백업 해시 불일치: {name}')
        pairs.append((saved, target))
    if len(pairs) != 2:
        raise RuntimeError('bundle/catalog 백업 쌍이 필요함')
    return pairs


def create_backup(game_path):
    game = Path(game_path).resolve()
    backup_dir = game / BACKUP_DIR_NAME
    buildid = get_steam_buildid(game_path)
    metadata_path = backup_dir / 'backup_state.json'
    bundle = find_bundle_path(game)
    if bundle is None:
        raise FileNotFoundError('게임 번들 없음')
    catalog = next((bundle.parent.parent / name for name in ['catalog.bin', 'catalog.json', 'catalog.bundle']
                    if (bundle.parent.parent / name).is_file()), None)
    if catalog is None:
        raise FileNotFoundError('게임 catalog 없음')
    with workspace_lock(bundle.parent.parent):
        if backup_dir.exists():
            metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
            same_build = metadata.get('buildid') == buildid if metadata else not PatchState(game_path).game_was_updated(buildid)
            if same_build:
                pairs = _backup_files(game, backup_dir, metadata)
                if not metadata:
                    write_json(metadata_path, {'buildid': buildid, 'legacy': True,
                        'files': {saved.name: {'path': str(target.relative_to(game)), 'sha256': sha256_file(saved)}
                                  for saved, target in pairs}})
                return str(backup_dir)
        with tempfile.TemporaryDirectory(prefix='.kr-backup-', dir=game) as tmp:
            stage = Path(tmp) / 'backup'; stage.mkdir()
            files = {}
            for path in [bundle, catalog]:
                shutil.copy2(path, stage / path.name)
                files[path.name] = {'path': str(path.relative_to(game)), 'sha256': sha256_file(stage / path.name)}
            write_json(stage / 'backup_state.json', {'buildid': buildid, 'files': files})
            archive = None
            if backup_dir.exists():
                archive = game / (BACKUP_DIR_NAME + '_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
                backup_dir.rename(archive)
            try:
                stage.rename(backup_dir)
            except BaseException:
                if archive:
                    archive.rename(backup_dir)
                raise
    return str(backup_dir)


def restore_backup(game_path):
    game = Path(game_path).resolve()
    backup_dir = game / BACKUP_DIR_NAME
    if not backup_dir.exists():
        return False
    metadata_path = backup_dir / 'backup_state.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    saved_build, current_build = metadata.get('buildid'), get_steam_buildid(game_path)
    if saved_build and current_build and saved_build != current_build:
        raise RuntimeError('다른 게임 빌드의 백업. Steam 무결성 검사로 복원 필요')
    pairs = _backup_files(game, backup_dir, metadata)
    aa = game / Path(BUNDLE_SUBDIR).parent
    with workspace_lock(aa):
        before = {target: target.read_bytes() for _, target in pairs}
        try:
            for saved, target in pairs:
                atomic_write(target, saved.read_bytes())
        except BaseException:
            for target, content in before.items():
                atomic_write(target, content)
            raise
        (game / PATCH_STATE_FILE).unlink(missing_ok=True)
    return True


# ━━━ 6. LELocalePatch CLI ━━━

def find_bundle_path(game_path):
    bundle = Path(game_path) / BUNDLE_SUBDIR / BUNDLE_FILENAME
    if bundle.exists():
        return bundle
    bundle_dir = Path(game_path) / BUNDLE_SUBDIR
    if bundle_dir.exists():
        for f in bundle_dir.glob("*korean*"):
            if f.suffix == ".bundle":
                return f
    return None

def run_lelocale_patch(lelocale_exe, bundle_path, action, json_source, progress_cb=None):
    if action == 'import':
        return import_locale(lelocale_exe, bundle_path, json_source)
    return run_checked(lelocale_exe, bundle_path, action, json_source)


def extract_checked(zip_path, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > 1000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
            raise ValueError('패치 압축파일 크기/항목 수 제한 초과')
        seen = set()
        for info in infos:
            name = info.filename
            target = destination / name
            if ('\\' in name or ':' in name or name.startswith('/') or
                    '..' in Path(name).parts or any(part.endswith((' ', '.')) for part in Path(name).parts) or
                    any(part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for part in Path(name).parts) or
                    (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError(f'허용되지 않는 압축 경로: {name}')
            target.resolve().relative_to(destination)
            normalized = name.casefold()
            if normalized in seen:
                raise ValueError(f'중복 압축 경로: {name}')
            seen.add(normalized)
        archive.extractall(destination)


# ━━━ 7. 델타 패칭 ━━━

def apply_delta_patch(original, delta, output):
    if not HAS_DETOOLS:
        return False
    try:
        with open(original, "rb") as fo, open(delta, "rb") as fd, open(output, "wb") as fout:
            detools.apply_patch(fo, fd, fout)
        return True
    except Exception:
        return False


# ━━━ 8. 메인 오케스트레이터 ━━━

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
            self._status(f"다운로드 중... {downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB ({pct:.0f}%)")

    def check_game_updated(self):
        current = get_steam_buildid(self.game_path)
        if current and self.state.game_was_updated(current):
            self._log(f"⚠️ 게임 업데이트 감지! (저장: {self.state.game_buildid} → 현재: {current})")
            return True
        return False

    def run(self):
        result = {"success": False, "version": "", "files": [], "message": ""}
        try:
            self._status("GitHub에서 최신 릴리즈 확인 중...")
            release = fetch_latest_release()
            tag = release.get("tag_name", "unknown")
            self._log(f"최신 릴리즈: {tag}")

            game_updated = self.check_game_updated()
            installed_bundle = find_bundle_path(self.game_path)
            content_matches = (installed_bundle is not None and self.state.data.get("bundle_hash") == sha256_file(installed_bundle))
            if not self.state.is_outdated(tag) and not game_updated and content_matches:
                msg = f"이미 최신 패치 적용됨 ({tag})"
                self._log(f"✅ {msg}")
                self._status(msg)
                result.update(success=True, version=tag, message=msg)
                return result

            assets = find_release_assets(release)
            if "patch_bundle" not in assets:
                raise RuntimeError(f"릴리즈에서 패치 번들(.zip)을 찾을 수 없습니다.\nhttps://github.com/{GITHUB_REPO}/releases")

            bundle_asset = assets["patch_bundle"]
            size_mb = bundle_asset["size"] / 1024 / 1024
            self._log(f"패치 번들: {bundle_asset['name']} ({size_mb:.1f}MB)")

            if 'checksums' not in assets:
                raise RuntimeError('SHA256SUMS 없는 릴리즈는 적용할 수 없음')
            checksums = download_and_parse_checksums(assets['checksums']['url'])
            expected_hash = checksums.get(bundle_asset['name'], '')
            if not re.fullmatch(r'[0-9a-fA-F]{64}', expected_hash):
                raise RuntimeError('패치 ZIP의 유효한 SHA256 체크섬 없음')

            bundle_path = find_bundle_path(self.game_path)
            if not bundle_path:
                raise RuntimeError("한국어 로컬라이제이션 번들을 찾을 수 없습니다.\n게임에서 언어를 한국어로 한번 설정한 후 다시 시도해주세요.")
            self._log(f"번들: {bundle_path}")

            with tempfile.TemporaryDirectory() as tmpdir:
                use_delta = False
                if 'delta_patch' in assets:
                    self._log('델타의 기준 번들 검증 정보 없음 — 전체 ZIP 사용')

                if not use_delta:
                    self._status(f"패치 번들 다운로드 중... ({size_mb:.1f}MB)")
                    zip_path = os.path.join(tmpdir, bundle_asset["name"])
                    download_file(bundle_asset["url"], zip_path, self._dl_progress)
                    self._log("다운로드 완료!")

                    if bundle_asset["name"] in checksums:
                        if not verify_checksum(zip_path, checksums[bundle_asset["name"]]):
                            raise RuntimeError("체크섬 불일치! 다시 시도해주세요.")
                        self._log("✅ SHA256 검증 통과")

                    self._status("압축 해제 중...")
                    extract_dir = os.path.join(tmpdir, "extracted")
                    os.makedirs(extract_dir)
                    extract_checked(zip_path, extract_dir)

                    lelocale_exe = self._find_file(extract_dir, "lelocalepatch.exe")
                    json_source = self._find_json_source(extract_dir)

                    self._log(f"LELocalePatch: {lelocale_exe}")
                    self._log(f"JSON 소스: {json_source}")
                    self._log(f"JSON 파일들: {self._list_json_files(json_source)}")

                    if not lelocale_exe or not self._list_json_files(json_source):
                        raise RuntimeError('LELocalePatch.exe 또는 번역 JSON이 없는 패키지')
                    self._status("기존 파일 백업 중...")
                    create_backup(self.game_path)

                    if lelocale_exe:
                        self._status("LELocalePatch로 번들 패치 중...")
                        run_lelocale_patch(lelocale_exe, str(bundle_path), "import", json_source)
                        files = self._list_json_files(json_source)
                        self._log(f"LELocalePatch: {len(files)}개 JSON 적용")


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
        """_ko.json이 가장 많은 폴더를 선택 (manifest.json 오염 방지)."""
        best_dir = extract_dir
        best_count = 0
        for root, dirs, files in os.walk(extract_dir):
            ko_jsons = [f for f in files if f.endswith("_ko.json")]
            if len(ko_jsons) > best_count:
                best_count = len(ko_jsons)
                best_dir = root
        if best_count == 0:
            for root, dirs, files in os.walk(extract_dir):
                jsons = [f for f in files if f.endswith("_ko.json")]
                if len(jsons) > best_count:
                    best_count = len(jsons)
                    best_dir = root
        return best_dir

    def _list_json_files(self, source):
        if os.path.isdir(source):
            return [f for f in os.listdir(source) if f.endswith("_ko.json")]
        return []

    def _apply_direct_copy(self, extract_dir):
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
        return applied


# ━━━ 9. GUI ━━━

def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class PatcherApp:
        BG, BG2, FG, ACCENT, WARN, ENTRY_BG = "#0f0f1a", "#161628", "#e0e0e0", "#ff4d6a", "#ffa726", "#1c1c3a"

        def __init__(self, root):
            self.root = root
            self.root.title(f"Last Epoch 한국어 번역패치 v{PATCHER_VERSION}")
            self.root.geometry("600x580")
            self.root.resizable(True, True)
            self.root.minsize(500, 400)
            self.root.configure(bg=self.BG)
            self.game_path = tk.StringVar()
            self.status_text = tk.StringVar(value="대기 중...")
            self._build_ui()
            self._auto_detect()
            self._check_new_patch_bg()

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
            self.entry_path = tk.Entry(fe, textvariable=self.game_path, font=("Consolas", 10), bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG, relief="flat", bd=5)
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
            self.do_force = tk.BooleanVar(value=False)
            for text, var in [("강제 재적용 (같은 버전이어도)", self.do_force)]:
                tk.Checkbutton(fo, text=text, variable=var, bg=self.BG, fg=self.FG, selectcolor=self.ENTRY_BG, activebackground=self.BG, activeforeground=self.FG, font=("맑은 고딕", 9)).pack(anchor="w")

            fp2 = tk.Frame(self.root, bg=self.BG)
            fp2.pack(fill="x", padx=24, pady=(10, 5))
            self.progress = ttk.Progressbar(fp2, mode="determinate", style="TProgressbar")
            self.progress.pack(fill="x")
            ttk.Label(fp2, textvariable=self.status_text, style="Status.TLabel").pack(anchor="w", pady=(3, 0))

            fl = tk.Frame(self.root, bg=self.BG)
            fl.pack(fill="both", expand=True, padx=24, pady=(5, 10))
            self.log_text = tk.Text(fl, height=7, bg=self.ENTRY_BG, fg="#7a7a9a", font=("Consolas", 8), relief="flat", bd=5, state="disabled")
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

        def _browse(self):
            p = filedialog.askdirectory(title="Last Epoch 폴더")
            if p:
                self.game_path.set(p)
                self._refresh_info(p)

        def _check_new_patch_bg(self):
            """백그라운드에서 새 번역 업데이트 확인."""
            def check():
                try:
                    gp = self.game_path.get().strip()
                    if not gp:
                        return
                    state = PatchState(gp)
                    release = fetch_latest_release()
                    tag = release.get("tag_name", "")
                    if state.is_outdated(tag):
                        self.root.after(0, lambda: self._notify_new_patch(tag, state.patch_version))
                except Exception:
                    pass
            threading.Thread(target=check, daemon=True).start()

        def _notify_new_patch(self, new_ver, current_ver):
            """새 번역 업데이트 알림 표시."""
            if current_ver:
                msg = f"  🔔 새 번역 업데이트 발견! ({current_ver} → {new_ver}) — 패치 적용을 눌러주세요"
            else:
                msg = f"  🔔 번역패치 {new_ver} 사용 가능 — 패치 적용을 눌러주세요"
            self.lbl_game.configure(text=msg, style="Warn.TLabel")
            self._log(msg.strip())
            self._status("새 번역 업데이트가 있습니다!")

        def _start(self):
            gp = self.game_path.get().strip()
            if not gp or not Path(gp).exists():
                messagebox.showerror("오류", "유효한 게임 경로를 지정해주세요.")
                return
            self.btn_apply.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self.progress["value"] = 0
            threading.Thread(target=self._run_patch, args=(gp, self.do_force.get()), daemon=True).start()

        def _run_patch(self, gp, force=False):
            orch = PatchOrchestrator(gp, log_cb=lambda m: self.root.after(0, self._log, m), status_cb=lambda m: self.root.after(0, self._status, m), progress_cb=lambda c, t: self.root.after(0, self._prog, c, t))
            if force:
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
            try:
                restored = restore_backup(gp)
            except Exception as exc:
                messagebox.showerror("복원 실패", str(exc))
                return
            if restored:
                self._log("✅ 복원 완료")
                self._refresh_info(gp)
                messagebox.showinfo("완료", "복원 완료!")
            else:
                messagebox.showerror("오류", "백업을 찾을 수 없습니다.")

    root = tk.Tk()
    PatcherApp(root)
    root.mainloop()


# ━━━ 10. CLI ━━━

def run_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Last Epoch 한국어 번역패치")
    parser.add_argument("--cli", action="store_true", help="CLI 모드")
    parser.add_argument("--path", help="게임 폴더 경로")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    print(f"\n{'=' * 55}\n  Last Epoch 한국어 번역패치 v{PATCHER_VERSION}\n  github.com/{GITHUB_REPO}\n{'=' * 55}\n")

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

    orch = PatchOrchestrator(gp, log_cb=lambda m: print(f"  {m}"), status_cb=lambda m: print(f"\n  >> {m}"), progress_cb=cli_prog)
    if args.force:
        orch.state.data.pop("patch_version", None)
    res = orch.run()
    print()
    if res["success"]:
        print(f"\n{'=' * 55}\n  ✅ {res['message']}\n  게임을 실행해주세요\n{'=' * 55}")
    else:
        print(f"\n  ❌ {res['message']}")
        sys.exit(1)


def main():
    if "--cli" in sys.argv or any(a.startswith("--") and a != "--cli" for a in sys.argv[1:]):
        run_cli()
    else:
        try:
            import tkinter
            run_gui()
        except ImportError:
            run_cli()

if __name__ == "__main__":
    main()
