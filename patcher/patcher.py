"""
Last Epoch 한국어 번역패치 원클릭 적용기
GitHub: fnrkp089/LETrans_Kr
"""

import os
from workbench_common import write_json, atomic_write, workspace_lock
from locale_runner import import_locale, run_checked
import unity_bundle
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
import time
import ssl
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

CA_BUNDLE = "cacert.pem"


def make_ssl_context():
    """Windows 인증서 저장소 + 패키지에 넣은 루트 인증서 묶음(cacert.pem).

    Windows는 루트 인증서를 필요할 때 받아 두는데 Python은 그걸 시키지 못해서, 저장소만 쓰면
    GitHub 루트가 아직 없는 PC에서 CERTIFICATE_VERIFY_FAILED가 남. 저장소도 같이 쓰는 이유:
    백신·회사 프록시가 HTTPS를 검사하는 PC는 그 인증서가 저장소에만 있음.
    """
    try:
        ctx = ssl.create_default_context()
    except Exception:
        raise RuntimeError("TLS 인증서 초기화 실패. 인증서 설정 확인 필요")
    bundle = Path(__file__).resolve().parent / CA_BUNDLE
    if not bundle.is_file():
        try:
            import certifi
            bundle = Path(certifi.where())
        except Exception:
            return ctx
    try:
        ctx.load_verify_locations(cafile=str(bundle))
    except Exception:
        pass
    return ctx


SSL_CONTEXT = make_ssl_context()

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
PATCHER_VERSION = "0.8.6"

GITHUB_API_RELEASES = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
GITHUB_API_LATEST = f"{GITHUB_API_RELEASES}/latest"
# 목록은 기본 30개까지만 오므로 한 번에 받을 수 있는 최대로 요청
GITHUB_API_RELEASE_LIST = f"{GITHUB_API_RELEASES}?per_page=100"
# 업데이트 내용 칸에 보여줄 첫 릴리즈 (시즌 5 공식 번역 기반으로 다시 시작한 버전)
HISTORY_SINCE = "v1.0.0"
USER_AGENT = f"LastEpoch-KR-Patcher/{PATCHER_VERSION}"
# 패처 패키지는 번역 릴리즈(vX.Y.Z)와 따로 patcher-vX.Y.Z 태그로 올림
PATCHER_TAG_PREFIX = "patcher-v"
PACKAGE_INFO = "package.json"
APP_ICON = "icon.ico"
# 패처 창 글꼴: 메이플스토리 서체 Bold (㈜넥슨코리아, 저작권 안내는 Maplestory-LICENSE.txt). 못 쓰면 맑은 고딕
UI_FONT_FILE = "Maplestory Bold.ttf"
UI_FONT_FAMILY = "메이플스토리"
UI_FONT_FALLBACK = "맑은 고딕"
# 배율 100% 기준 창 크기와, 이보다 낮으면 좁은 화면용 배치로 바꾸는 높이
WINDOW_SIZE = (1140, 840)
COMPACT_BELOW = 760

BUNDLE_SUBDIR = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "StandaloneWindows64"
BUNDLE_FILENAME = "localization-string-tables-korean(ko)_assets_all.bundle"
CATALOG_RELPATH = Path("Last Epoch_Data") / "StreamingAssets" / "aa" / "catalog.bin"

PATCH_STATE_FILE = "kr_patch_state.json"
BACKUP_DIR_NAME = "kr_patch_backup"
# 상태 파일에서 번역 패치가 쓰는 항목. 그 밖(폰트 기록 "font", 번역 사용 여부 "translate")은 번역을 복원해도 남김
TRANSLATION_STATE_KEYS = ("patch_version", "patch_date", "game_buildid", "bundle_hash", "patcher_version", "files_applied", "shared_bundle_hash")
RESTORE_PARTS = {"all": "번역과 폰트 모두", "translation": "번역만", "font": "폰트만"}

# 모든 언어가 같이 쓰는 키 이름표. 게임이 찾는 이름이 여기 없으면 번역이 있어도 키 번호가 그대로 나옴
SHARED_BUNDLE_FILENAME = "localization-assets-shared_assets_all.bundle"
# {게임에 빠진 키 이름: 같은 글을 가리키는 기존 키 이름}
# 1156(마나 및 마나 재생 증가): 제련 창이 찾는 DisplayName 키가 없어 이름이 "1156"으로 나옴
KEY_ALIASES = {"Item_Affix_1156_DisplayName": "Item_Affix_1156_LootFilterOverride"}

# 폰트 패치 (LEFontPatch): 한국어 폰트 에셋은 번역 번들이 아니라 resources.assets와,
# UI 대부분이 쓰는 사본이 든 PermaLoad.bundle 두 곳에 있음. 한쪽만 바꾸면 글자마다 폰트가 섞임
RESOURCES_RELPATH = Path("Last Epoch_Data") / "resources.assets"
FONT_BUNDLE_RELPATH = Path("Last Epoch_Data") / "StreamingAssets" / "LEAssetBundles" / "PermaLoad.bundle"
# TextMeshPro 기본 폰트(Caladea) 사본이 든 파일. 굵게 표시되는 글자(체력·마나 숫자, 단축키 등)는
# 영문·숫자를 여기서 가져오므로, 여기서도 빼야 한국어 폰트로 넘어감
FONT_SHARED_RELPATH = Path("Last Epoch_Data") / "sharedassets0.assets"
FONT_BACKUP_DIR_NAME = "kr_font_backup"
FONT_TOOL_NAME = "LEFontPatch.exe"
KR_FONT_ASSETS = ["NotoSerifKR-Regular SDF (Body)", "HahmletKR-Medium SDF (Title)"]
# 게임은 영문·숫자·기호를 라틴 폰트에서 먼저 찾으므로, 한국어 폰트로 넘기려면 다른 폰트에서 빼야 함.
# "*": 선택한 폰트 파일에 있는 글자 전부
FONT_ALL_CHARACTERS = "*"
FONT_MODES = {
    "none": "게임 기본 폰트",
    "bold": "진한 고딕 (Pretendard Bold)",
    "custom": "직접 선택 (TTF/OTF 파일)",
}

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
        elif name == FONT_TOOL_NAME.lower():
            assets["font_tool"] = info
    return assets


def release_highlights(body):
    """릴리즈 노트에서 첫 '## ' 섹션(주요 작업)의 줄만 추림. 섹션이 없으면 본문 전체.

    노트마다 Defender 안내·사용법·복원이 똑같이 붙어 있어 전부 보여주면 바뀐 내용이 묻힘.
    """
    lines = (body or "").replace("\r\n", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    if starts:
        end = starts[1] if len(starts) > 1 else len(lines)
        lines = lines[starts[0] + 1:end]
    else:
        lines = [line for line in lines if not line.startswith("# ")]
    lines = [line.rstrip() for line in lines if line.strip() != "---"]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def release_history(releases, current_version, since=HISTORY_SINCE):
    """since부터의 번역 릴리즈 전부(최신순). 적용된 버전보다 새것에는 new 표시."""
    # 번역 릴리즈(vX.Y.Z)만. 패처 패키지 릴리즈(patcher-vX.Y.Z)는 제외
    published = [r for r in releases if re.match(r"v\d", r.get("tag_name") or "")
                 and not r.get("draft") and not r.get("prerelease")
                 and parse_version(r["tag_name"]) >= parse_version(since)]
    published.sort(key=lambda r: parse_version(r["tag_name"]), reverse=True)
    return [{"tag": r["tag_name"], "title": r.get("name") or r["tag_name"], "date": (r.get("published_at") or "")[:10],
             "new": bool(current_version) and parse_version(r["tag_name"]) > parse_version(current_version),
             "lines": release_highlights(r.get("body"))} for r in published]


def markdown_spans(line):
    """릴리즈 노트 한 줄 → (머리 기호, [(글, 굵게 여부)]). 링크는 글만, 백틱은 뗌."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line.strip()).replace("`", "")
    lead = ""
    if text.startswith(("- ", "* ")):
        lead, text = "• ", text[2:]
    spans = [(part, i % 2 == 1) for i, part in enumerate(text.split("**")) if part]
    return lead, spans


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

    @property
    def translate(self):
        """번역 패치를 쓰는지. False면 게임 공식 번역을 그대로 두고 폰트만 바꾸는 사용자."""
        return bool(self.data.get("translate", True))

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
    # bundle/catalog 쌍은 필수, 공용 키 번들은 고쳤을 때만 들어 있음
    if len([name for name in names if name != SHARED_BUNDLE_FILENAME]) != 2:
        raise RuntimeError('bundle/catalog 백업 쌍이 필요함')
    return pairs


def _kept_by_update(game, backup_dir, metadata):
    """게임 빌드가 바뀌었어도 Steam이 교체하지 않아 우리가 패치한 그대로인 파일 이름들.

    그런 파일의 원본은 이전 빌드의 백업에만 있음: 지금 파일을 새로 백업하면 패치본이 원본 자리에 들어감.
    """
    state, kept = PatchState(game).data, set()
    for name, record in (metadata.get('files') or {}).items():
        saved, target = backup_dir / name, game / record['path']
        if not (saved.is_file() and target.is_file()) or sha256_file(saved) != record.get('sha256'):
            continue
        if name == SHARED_BUNDLE_FILENAME:
            ours = sha256_file(target) == state.get('shared_bundle_hash')
        elif name.startswith('catalog'):
            # LELocalePatch는 카탈로그에서 번들 CRC만 0으로 지움. 새 빌드의 카탈로그면 0이 아닌 값이 달라짐
            current, original = target.read_bytes(), saved.read_bytes()
            ours = len(current) == len(original) and not any(a for a, b in zip(current, original) if a != b)
        else:
            ours = sha256_file(target) == state.get('bundle_hash')
        if ours:
            kept.add(name)
    return kept


class _StagingDir:
    """parent 아래에 만들었다가 끝나면 지우는 작업 폴더.

    tempfile.mkdtemp를 쓰지 않는 이유: Windows에서 만든 계정만 쓸 수 있는 권한이 걸리고, 그 안에서 만든 폴더를
    밖으로 옮겨도 그 권한이 따라감. 그러면 패처를 관리자 권한으로 한 번 실행한 뒤 일반 권한으로 실행할 때
    백업·패처 폴더를 열지 못함(Permission denied).
    """

    def __init__(self, parent, prefix):
        self.path = Path(parent) / f"{prefix}{os.getpid()}-{datetime.now().strftime('%H%M%S%f')}"

    def __enter__(self):
        self.path.mkdir()
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path, ignore_errors=True)


def inherit_permissions(folder):
    """폴더와 그 안의 권한을 상위 폴더에서 물려받게 되돌림. 이전 버전이 만든 백업 폴더용, 실패해도 그대로 진행."""
    if os.name != "nt" or not Path(folder).is_dir():
        return
    try:
        subprocess.run(["icacls", str(folder), "/reset", "/T", "/C", "/Q"], stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


def explain_error(exc):
    """오류 문구. 백업 폴더 권한 문제에는 해결 방법을 덧붙임."""
    message = str(exc)
    if isinstance(exc, PermissionError) and BACKUP_DIR_NAME in message:
        message += ("\n\n백업 폴더를 열 권한이 없습니다. 예전에 패처를 '관리자 권한으로 실행'한 적이 있으면 생기는 문제입니다.\n"
                    "바탕 화면의 패처 바로가기를 우클릭 → '관리자 권한으로 실행'으로 한 번 실행해 패치 적용을 누르면 "
                    "권한이 고쳐지고, 다음부터는 평소처럼 실행해도 됩니다.")
    return message


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
            inherit_permissions(backup_dir)
            metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
            same_build = metadata.get('buildid') == buildid if metadata else not PatchState(game_path).game_was_updated(buildid)
            if same_build:
                pairs = _backup_files(game, backup_dir, metadata)
                if not metadata:
                    write_json(metadata_path, {'buildid': buildid, 'legacy': True,
                        'files': {saved.name: {'path': str(target.relative_to(game)), 'sha256': sha256_file(saved)}
                                  for saved, target in pairs}})
                return str(backup_dir)
        with _StagingDir(game, '.kr-backup-') as tmp:
            stage = tmp / 'backup'; stage.mkdir()
            files = {}
            old = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
            # 게임 업데이트가 우리가 패치한 파일을 그대로 뒀으면 그 원본은 이전 백업에만 있음
            kept = _kept_by_update(game, backup_dir, old)
            for path in [bundle, catalog]:
                shutil.copy2(backup_dir / path.name if path.name in kept else path, stage / path.name)
                files[path.name] = {'path': str(path.relative_to(game)), 'sha256': sha256_file(stage / path.name)}
            shared = bundle.parent / SHARED_BUNDLE_FILENAME
            if shared.name in kept:
                shutil.copy2(backup_dir / shared.name, stage / shared.name)
                files[shared.name] = old['files'][shared.name]
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
    inherit_permissions(backup_dir)
    metadata_path = backup_dir / 'backup_state.json'
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    saved_build, current_build = metadata.get('buildid'), get_steam_buildid(game_path)
    # 빌드가 달라도 Steam이 이 파일들을 교체하지 않았으면 백업은 여전히 그 원본
    if (saved_build and current_build and saved_build != current_build
            and not (metadata.get('files') and _kept_by_update(game, backup_dir, metadata) == set(metadata['files']))):
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
        # 번역 기록만 지움: 폰트 기록까지 지우면 적용된 폰트를 패처가 모르게 됨
        state = PatchState(game_path)
        for key in TRANSLATION_STATE_KEYS:
            state.data.pop(key, None)
        if state.data:
            state.save()
        else:
            (game / PATCH_STATE_FILE).unlink(missing_ok=True)
    return True


def apply_key_aliases(game_path, state):
    """공용 키 번들에 빠진 키 이름(KEY_ALIASES)을 추가. 추가한 이름들 반환.

    이미 있으면(게임이 고쳤거나 전에 추가함) 파일을 건드리지 않고 [] 반환.
    고치기 전 원본은 번역 백업에 넣어 복원 때 카탈로그와 같이 되돌림: 카탈로그만 원본이면 고친 번들이 CRC 검사에 걸림.
    """
    game = Path(game_path).resolve()
    shared = game / BUNDLE_SUBDIR / SHARED_BUNDLE_FILENAME
    if not shared.is_file():
        return []
    backup_dir = game / BACKUP_DIR_NAME
    metadata_path = backup_dir / 'backup_state.json'
    with workspace_lock(game / Path(BUNDLE_SUBDIR).parent):
        raw = shared.read_bytes()
        patched, added = unity_bundle.add_key_aliases(raw, KEY_ALIASES)
        if not added:
            return []
        if not metadata_path.is_file():
            raise RuntimeError('번역 백업이 없어 공용 키 번들을 고치지 않음')
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if not metadata.get('files'):
            raise RuntimeError('번역 백업 정보가 없어 공용 키 번들을 고치지 않음')
        atomic_write(backup_dir / shared.name, raw)
        metadata['files'][shared.name] = {'path': str(shared.relative_to(game)), 'sha256': hashlib.sha256(raw).hexdigest()}
        write_json(metadata_path, metadata)
        atomic_write(shared, patched)
        state.data['shared_bundle_hash'] = hashlib.sha256(patched).hexdigest()
        state.save()
    return added


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

def work_root():
    """임시 작업 폴더를 만들 위치. 쓸 수 없으면 None (시스템 TEMP).

    시스템 TEMP가 '문서' 아래로 바뀌어 있는 PC(예: ESTsoft CreatorTemp)에서는 랜섬웨어 차단 기능이
    LELocalePatch의 파일 생성을 막아 "Could not find file ...catalog.bin"으로 실패하므로 TEMP에 의존하지 않음.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    root = Path(base) / "LETransKr" / "tmp"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return str(root)


def run_lelocale_patch(lelocale_exe, bundle_path, action, json_source, progress_cb=None):
    if action == 'import':
        return import_locale(lelocale_exe, bundle_path, json_source, workdir=work_root())
    return run_checked(lelocale_exe, bundle_path, action, json_source)


def extract_checked(zip_path, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > 1000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
            raise ValueError('패치 압축파일 크기/항목 수 제한 초과')
        seen = set()
        for info in infos:
            name = info.orig_filename
            if name != info.filename or chr(0) in name:
                raise ValueError(f'정규화 전후가 다른 압축 경로: {name!r}')
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


# ━━━ 6b. 폰트 패치 (LEFontPatch CLI) ━━━

def normalize_font(font):
    """{'mode': none|bold|custom, 'ttf': 경로, 'all_text': 영문·숫자·기호 포함 여부} 검증.

    custom은 파일 해시까지 포함해 변경 감지에 사용. all_text 기본값은 True.
    """
    mode = (font or {}).get("mode", "none")
    if mode not in FONT_MODES:
        raise ValueError(f"알 수 없는 폰트 설정: {mode}")
    if mode == "none":
        return {"mode": mode}
    all_text = bool(font.get("all_text", True))
    if mode != "custom":
        return {"mode": mode, "all_text": all_text}
    ttf = Path(font.get("ttf") or "")
    if not ttf.is_file() or ttf.suffix.lower() not in (".ttf", ".otf"):
        raise ValueError("폰트 파일(.ttf/.otf)을 선택해주세요.")
    return {"mode": mode, "all_text": all_text, "ttf": str(ttf.resolve()), "ttf_sha256": sha256_file(ttf)}


def _font_files(game):
    """폰트 에셋이 든 게임 파일들 (게임 폴더 기준 상대 경로). resources.assets 외에는 게임 버전에 따라 없을 수 있음."""
    return [RESOURCES_RELPATH] + [rel for rel in (FONT_SHARED_RELPATH, FONT_BUNDLE_RELPATH) if (Path(game) / rel).is_file()]


def _patched_hashes(state):
    saved = state.data.get("font") or {}
    if "patched_sha256" in saved:  # v0.7.0: resources.assets만 패치하던 형식
        return {RESOURCES_RELPATH.as_posix(): saved["patched_sha256"]}
    return saved.get("patched") or {}


def font_is_applied(game_path, state, font):
    """원하는 폰트가 이미 적용돼 있고 게임 업데이트로 되돌려지지 않았는지."""
    saved = state.data.get("font") or {}
    wanted = normalize_font(font)
    game = Path(game_path)
    patched = _patched_hashes(state)
    ours = {rel.as_posix(): (game / rel).is_file() and sha256_file(game / rel) == patched.get(rel.as_posix())
            for rel in _font_files(game)}
    if wanted["mode"] == "none":
        # 우리가 만든 파일이 하나도 없으면 원본으로 본다 (Steam이 교체했거나 한 번도 적용 안 함)
        return not any(ours.values())
    return {k: saved.get(k) for k in wanted} == wanted and all(ours.values())


def _copy_replace(source, target):
    """큰 파일을 같은 폴더의 임시 파일로 복사한 뒤 교체. 게임 실행 중이면 교체 단계에서 실패."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, name)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _font_originals(game, state):
    """폰트 파일마다 원본 백업을 확보해 {상대 경로: (백업 경로, 원본 해시)} 반환.

    현재 파일이 우리가 패치한 결과이거나 같은 게임 빌드의 백업이 있으면 그 백업이 원본.
    그 외(첫 적용, 게임 업데이트로 파일 교체)에는 현재 파일을 원본으로 새로 백업.
    """
    backup_dir = game / FONT_BACKUP_DIR_NAME
    metadata_path = backup_dir / "backup_state.json"
    buildid = get_steam_buildid(game)
    metadata = {}
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except ValueError:
            metadata = {}
    recorded = dict(metadata.get("files") or {})
    if "sha256" in metadata:  # v0.7.0 형식
        recorded[RESOURCES_RELPATH.as_posix()] = metadata["sha256"]
    patched = _patched_hashes(state)
    originals, changed = {}, False
    for rel in _font_files(game):
        key, target, saved = rel.as_posix(), game / rel, backup_dir / rel.name
        current = sha256_file(target)
        ours = current == patched.get(key)
        if key in recorded and saved.is_file() and (ours or metadata.get("buildid") == buildid or recorded[key] == current):
            if sha256_file(saved) != recorded[key]:
                raise RuntimeError(f"폰트 원본 백업 손상({rel.name}). Steam 무결성 검사 후 다시 시도해주세요.")
        elif ours:
            raise RuntimeError(f"폰트 원본 백업 없음({rel.name}). Steam 무결성 검사 후 다시 시도해주세요.")
        else:
            _copy_replace(target, saved)
            if sha256_file(saved) != current:
                raise RuntimeError("폰트 원본 백업 중 게임 파일이 변경됨. 다시 실행 필요")
            recorded[key], changed = current, True
        originals[rel] = (saved, recorded[key])
    if changed or "files" not in metadata:
        write_json(metadata_path, {"buildid": buildid, "files": recorded})
    return originals


def _write_font_log(result):
    """실패한 LEFontPatch의 전체 출력을 남겨 제보받을 때 원인을 볼 수 있게 함. 저장 못 하면 None."""
    root = work_root()
    if not root:
        return None
    path = Path(root).parent / "font_tool.log"
    try:
        path.write_text(f"{datetime.now().isoformat()} 패처 {PATCHER_VERSION} 종료 코드 {result.returncode}\n"
                        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n", encoding="utf-8")
    except OSError:
        return None
    return path


def run_font_tool(tool_exe, game, package, bundles=0, timeout=1800):
    result = subprocess.run([str(tool_exe), str(game), str(package)], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = [line.strip() for line in result.stdout.splitlines()]
    if result.returncode or "Error" in lines or "Done!" not in lines:
        log_path = _write_font_log(result)
        if result.returncode == 2:
            reason = "게임에서 한국어 폰트를 찾지 못했습니다. 게임을 최신 버전으로 업데이트했는지 확인해주세요."
        elif result.stderr.strip():
            reason = result.stderr.strip().splitlines()[0]
        else:
            # 예외 메시지 없이 끝남: 도구가 중간에 강제 종료된 경우(백신 차단, 메모리 부족 등)
            last = next((line for line in reversed(lines) if line and not line.startswith("Enter to exit")), "출력 없음")
            reason = f"도구가 중간에 종료됨 (종료 코드 {result.returncode}, 마지막 단계: {last}). 백신이 차단했는지 확인해주세요."
        raise RuntimeError(f"LEFontPatch 실패: {reason}" + (f"\n로그: {log_path}" if log_path else ""))
    replaced = [line for line in lines if line.startswith("Made dynamic:")]
    in_bundles = [line for line in lines if line.startswith("Made dynamic in bundle:")]
    if len(replaced) != len(KR_FONT_ASSETS) or len(in_bundles) != len(KR_FONT_ASSETS) * bundles:
        raise RuntimeError(f"한국어 폰트 일부만 교체됨 ({len(replaced)}+{len(in_bundles)}개). 게임 폰트 구성이 바뀐 것 같습니다.")
    return replaced + in_bundles


def apply_font(game_path, state, font, tool_exe=None):
    """한국어 폰트를 교체하거나(mode bold/custom) 원본으로 되돌림(mode none). 적용된 설정 반환."""
    game = Path(game_path).resolve()
    wanted = normalize_font(font)
    if not (game / RESOURCES_RELPATH).is_file():
        raise FileNotFoundError(f"게임 파일 없음: {RESOURCES_RELPATH}")
    if wanted["mode"] != "none" and not (tool_exe and Path(tool_exe).is_file()):
        raise FileNotFoundError(f"{FONT_TOOL_NAME} 없음")
    files = _font_files(game)
    try:
        for rel in files:
            with open(game / rel, "r+b"):
                pass
    except PermissionError:
        raise RuntimeError("게임이 실행 중입니다. 게임을 종료한 뒤 다시 시도해주세요.") from None

    def restore(originals):
        for rel, (saved, original_hash) in originals.items():
            if sha256_file(game / rel) != original_hash:
                _copy_replace(saved, game / rel)

    with workspace_lock(game / FONT_BACKUP_DIR_NAME):
        originals = _font_originals(game, state)
        # 항상 원본에서 다시 시작: 다른 폰트로 바꿀 때 이전 패치가 남지 않게
        restore(originals)
        state.data.pop("font", None)
        state.save()
        if wanted["mode"] == "none":
            return wanted
        with tempfile.TemporaryDirectory(prefix="le-font-", dir=work_root()) as tmp:
            package = Path(tmp)
            if wanted["mode"] == "custom":
                source = {"ttf": "fonts/custom" + Path(wanted["ttf"]).suffix.lower()}
                (package / "fonts").mkdir()
                shutil.copyfile(wanted["ttf"], package / source["ttf"])
                if sha256_file(package / source["ttf"]) != wanted["ttf_sha256"]:
                    raise RuntimeError("적용 준비 중 폰트 파일이 변경됨. 다시 실행 필요")
            else:
                source = {"fontAsset": "Pretendard-Bold"}  # 게임에 이미 들어 있는 폰트 파일
            # 번들 경로는 도구 기준(Last Epoch_Data 아래)
            bundles = [rel.relative_to(RESOURCES_RELPATH.parent).as_posix() for rel in files if rel == FONT_BUNDLE_RELPATH]
            manifest = {
                "dynamicFonts": {name: dict(source) for name in KR_FONT_ASSETS},
                "dynamicFontBundles": bundles,
                "cancelIfNoFontReplaced": True,
            }
            if wanted["all_text"]:
                manifest["dynamicFontCharacters"] = FONT_ALL_CHARACTERS
                if FONT_SHARED_RELPATH in files:
                    manifest["extraAssetsFile"] = FONT_SHARED_RELPATH.relative_to(RESOURCES_RELPATH.parent).as_posix()
            write_json(package / "manifest.json", manifest)
            try:
                replaced = run_font_tool(tool_exe, game, package, len(bundles))
            except BaseException:
                restore(originals)
                raise
        state.data["font"] = dict(wanted, patched={rel.as_posix(): sha256_file(game / rel) for rel in files},
                                  fonts=replaced, date=datetime.now().isoformat())
        state.save()
    return wanted


def restore_font(game_path):
    """폰트를 원본으로 되돌림. 되돌릴 것이 없으면 False."""
    state = PatchState(game_path)
    if font_is_applied(game_path, state, {"mode": "none"}):
        if state.data.pop("font", None) is not None:
            state.save()
        return False
    apply_font(game_path, state, {"mode": "none"})
    return True


def _process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # 접근 거부 = 다른 권한으로 실행 중인 프로세스
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def clear_stale_locks(game_path):
    """적용 도중 패처가 꺼지면 남는 잠금 파일 정리. 지운 경로 목록 반환.

    잠금을 만든 프로세스가 이미 없을 때만 지움. 살아 있는 프로세스의 잠금은 그대로 둬서 동시 실행을 막음.
    """
    game = Path(game_path)
    removed = []
    for folder in [game / FONT_BACKUP_DIR_NAME, game / Path(BUNDLE_SUBDIR).parent]:
        lock = folder / ".workbench.lock"
        if not lock.is_file():
            continue
        try:
            pid = int(json.loads(lock.read_text(encoding="utf-8")).get("pid"))
        except (OSError, ValueError, TypeError, AttributeError):
            pid = None  # 쓰다 만 잠금 파일
        if pid is not None and pid != os.getpid() and _process_alive(pid):
            continue
        try:
            lock.unlink()
            removed.append(lock)
        except OSError:
            pass
    return removed


def restore_all(game_path):
    """폰트 → 번역 순서로 복원. 하나라도 복원했으면 True."""
    clear_stale_locks(game_path)
    font = restore_font(game_path)
    return restore_backup(game_path) or font


def restore_part(game_path, what="all"):
    """RESTORE_PARTS 중 고른 것만 원본으로 되돌림. 되돌린 것이 있으면 True."""
    if what not in RESTORE_PARTS:
        raise ValueError(f"알 수 없는 복원 대상: {what}")
    if what == "all":
        return restore_all(game_path)
    clear_stale_locks(game_path)
    if what == "font":
        return restore_font(game_path)
    restored = restore_backup(game_path)
    if restored:
        # 번역만 되돌린 사람은 폰트만 쓰려는 것: 다음 적용 때 번역이 다시 깔리지 않게 기억
        state = PatchState(game_path)
        state.data["translate"] = False
        state.save()
    return restored


def find_local_font_tool():
    """패처 스크립트 옆이나 패키지 폴더에 둔 LEFontPatch.exe (오프라인·개발용)."""
    here = Path(__file__).resolve().parent
    for folder in [here, here.parent, Path(sys.executable).parent]:
        if (folder / FONT_TOOL_NAME).is_file():
            return folder / FONT_TOOL_NAME
    return None


def fetch_font_tool(release, progress_cb=None):
    """릴리즈의 LEFontPatch.exe를 SHA256 검증 후 캐시에 저장해 경로 반환."""
    local = find_local_font_tool()
    if local:
        return local
    releases = [release] + [r for r in github_api_get(GITHUB_API_RELEASE_LIST) if r.get("tag_name") != release.get("tag_name")]
    for candidate in releases:
        assets = find_release_assets(candidate)
        if "font_tool" not in assets or "checksums" not in assets:
            continue
        expected = download_and_parse_checksums(assets["checksums"]["url"]).get(assets["font_tool"]["name"], "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            continue
        cache = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "LETransKr" / "tools"
        cached = cache / f"{Path(FONT_TOOL_NAME).stem}-{expected[:16].lower()}.exe"
        if cached.is_file() and verify_checksum(cached, expected):
            return cached
        cache.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".font-tool-", suffix=".tmp", dir=cache)
        os.close(fd)
        try:
            download_file(assets["font_tool"]["url"], name, progress_cb)
            if not verify_checksum(name, expected):
                raise RuntimeError(f"{FONT_TOOL_NAME} 체크섬 불일치! 다시 시도해주세요.")
            os.replace(name, cached)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return cached
    raise RuntimeError(f"릴리즈에서 {FONT_TOOL_NAME}(체크섬 포함)을 찾을 수 없습니다.")


# ━━━ 6c. 패처 자체 업데이트 ━━━

def package_dir():
    """배포 패키지(app\\ + runtime\\)로 실행 중이면 app 폴더, 소스로 실행 중이면 None."""
    app = Path(__file__).resolve().parent
    return app if (app / PACKAGE_INFO).is_file() and (app.parent / "runtime").is_dir() else None


def find_patcher_update(releases, current=PATCHER_VERSION):
    """현재보다 새 패처 릴리즈(patcher-vX.Y.Z) 중 가장 높은 버전. 없으면 None."""
    best = None
    for r in releases or []:
        tag = r.get("tag_name") or ""
        if not tag.startswith(PATCHER_TAG_PREFIX) or r.get("draft") or r.get("prerelease"):
            continue
        version = tag[len(PATCHER_TAG_PREFIX):]
        if parse_version(version) <= parse_version(best["version"] if best else current):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in r.get("assets", [])}
        name = f"LastEpoch_KR_Patcher-app-v{version}.zip"
        best = {"version": version, "tag": tag, "page": r.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases",
                "name": name, "url": assets.get(name), "checksums": assets.get("SHA256SUMS")}
    return best


def apply_patcher_update(update, progress_cb=None):
    """새 패처 소스(app zip)를 받아 SHA256·실행 확인 후 app 폴더를 통째로 교체.

    runtime\\은 실행 중이라 바꿀 수 없으므로, Python 버전이 달라진 업데이트는 거부하고 새 패키지를 받게 함.
    """
    app = package_dir()
    if not app:
        raise RuntimeError("배포 패키지로 실행 중이 아니어서 자동 업데이트할 수 없습니다.")
    if not update.get("url") or not update.get("checksums"):
        raise RuntimeError("릴리즈에 패처 업데이트 파일이 없습니다.")
    expected = download_and_parse_checksums(update["checksums"]).get(update["name"], "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        raise RuntimeError("패처 업데이트의 체크섬 정보가 없습니다.")
    try:
        stage = _StagingDir(app.parent, ".update-").path
        stage.mkdir()
    except OSError as exc:
        raise RuntimeError(f"패처 폴더에 쓸 수 없습니다: {exc}")
    old = app.with_name("app.old")
    try:
        archive = stage / update["name"]
        download_file(update["url"], archive, progress_cb)
        if not verify_checksum(archive, expected):
            raise RuntimeError("패처 업데이트 체크섬 불일치! 다시 시도해주세요.")
        new = stage / "app"
        new.mkdir()
        extract_checked(archive, new)
        info = json.loads((new / PACKAGE_INFO).read_text(encoding="utf-8"))
        if info.get("patcher") != update["version"]:
            raise RuntimeError("패처 업데이트 파일의 버전이 릴리즈와 다릅니다.")
        if info.get("python") != json.loads((app / PACKAGE_INFO).read_text(encoding="utf-8")).get("python"):
            raise RuntimeError("이번 업데이트는 Python 런타임도 바뀌어서 새 패키지를 직접 받아야 합니다.")
        # 새 소스가 지금 런타임에서 뜨는지 확인한 뒤에 교체
        check = subprocess.run(
            [str(Path(sys.executable).with_name("python.exe")), "-B", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import patcher; print(patcher.PATCHER_VERSION)", str(new)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check.returncode != 0 or check.stdout.strip() != update["version"]:
            raise RuntimeError("새 패처가 실행 확인을 통과하지 못했습니다.")
        shutil.rmtree(old, ignore_errors=True)
        try:
            os.replace(app, old)
        except OSError as exc:
            raise RuntimeError(f"패처 폴더를 바꿀 수 없습니다: {exc}")
        try:
            os.replace(new, app)
        except OSError as exc:
            os.replace(old, app)
            raise RuntimeError(f"패처 폴더를 바꿀 수 없습니다: {exc}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return app / "patcher.py"


def restart_patcher(script):
    subprocess.Popen([sys.executable, "-B", str(script)] + sys.argv[1:], close_fds=True)


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
    def __init__(self, game_path, log_cb=None, status_cb=None, progress_cb=None, font=None, working_cb=None, translate=True):
        self.game_path = game_path
        self.font = font  # None: 폰트는 건드리지 않음 / {'mode': ..., 'ttf': ...}
        self.translate = translate  # False: 번역은 건드리지 않음 (게임에 있는 번역 그대로)
        self._release = None
        self.state = PatchState(game_path)
        self._log = log_cb or (lambda msg: log.info(msg))
        self._status = status_cb or (lambda msg: None)
        self._progress = progress_cb or (lambda val, total: None)
        # 진행률을 알 수 없는 단계(압축 해제, 번들 패치, 폰트 적용)에 들어갈 때 호출
        self._working = working_cb or (lambda: None)

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
        for lock in clear_stale_locks(self.game_path):
            self._log(f"이전 실행이 남긴 잠금 정리: {lock}")
        if self.translate:
            result = self._run_translation()
        else:
            result = {"success": True, "version": self.state.patch_version or "", "files": [], "message": "번역은 건드리지 않았습니다."}
            self._log("번역 패치: 건너뜀")
        if result["success"] and self.font is not None:
            try:
                self._run_font(result)
            except Exception as e:
                result.update(success=False, message=("번역 패치는 적용됨. " if self.translate else "") + f"폰트 적용 실패: {e}")
                self._log(f"❌ 폰트 오류: {e}")
                self._status("❌ 폰트 적용 실패")
                log.exception("Font patch failed")
        if result["success"]:
            self._remember_translate()
        return result

    def _remember_translate(self):
        """번역을 쓰는지 기억해 다음 실행의 체크 상태와 새 번역 알림에 씀."""
        if self.state.translate == self.translate:
            return
        self.state.data["translate"] = self.translate
        try:
            self.state.save()
        except OSError as e:
            self._log(f"⚠️ 설정 저장 실패: {e}")

    def _run_font(self, result):
        wanted = normalize_font(self.font)
        label = FONT_MODES[wanted["mode"]]
        if font_is_applied(self.game_path, self.state, wanted):
            self._log(f"폰트: {label} (변경 없음)")
            if not self.translate:
                result["message"] += f"\n폰트: {label} (변경 없음)"
                self._status(f"폰트: {label} (변경 없음)")
            return
        tool = None
        if wanted["mode"] != "none":
            self._status("폰트 도구 준비 중...")
            # 번역을 건너뛰면 릴리즈를 아직 안 받았음 (옆에 둔 도구가 있으면 받을 필요 없음)
            tool = find_local_font_tool() or fetch_font_tool(self._release or fetch_latest_release(), self._dl_progress)
            self._log(f"LEFontPatch: {tool}")
        self._working()
        self._status("폰트 적용 중... (1~2분 걸릴 수 있습니다)")
        apply_font(self.game_path, self.state, wanted, tool)
        result["message"] += f"\n폰트: {label}"
        self._log(f"✅ 폰트 적용: {label}")
        self._status(f"✅ 폰트 적용 완료 ({label})")

    def _run_translation(self):
        result = {"success": False, "version": "", "files": [], "message": ""}
        try:
            self._status("GitHub에서 최신 릴리즈 확인 중...")
            release = self._release = fetch_latest_release()
            tag = release.get("tag_name", "unknown")
            self._log(f"최신 릴리즈: {tag}")

            game_updated = self.check_game_updated()
            installed_bundle = find_bundle_path(self.game_path)
            content_matches = (installed_bundle is not None and self.state.data.get("bundle_hash") == sha256_file(installed_bundle))
            if not self.state.is_outdated(tag) and not game_updated and content_matches:
                # 번역은 그대로여도 새 패처가 고치는 키 이름은 여기서 적용 (원본 백업이 있을 때만)
                self._fix_keys(check_backup=True)
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

            with tempfile.TemporaryDirectory(prefix="le-patch-", dir=work_root()) as tmpdir:
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

                    self._working()
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
                        self._fix_keys()

                current_buildid = get_steam_buildid(self.game_path)
                bh = sha256_file(str(bundle_path))
                applied_files = files if not use_delta else ["(delta)"]
                self.state.update(tag, current_buildid, bh, applied_files)

                msg = f"패치 적용 완료! ({tag}, {len(applied_files)}개 파일)"
                result.update(success=True, version=tag, files=applied_files, message=msg)
                self._log(f"✅ {msg}")
                self._status(f"✅ {msg}")

        except Exception as e:
            result["message"] = explain_error(e)
            self._log(f"❌ 오류: {e}")
            self._status("❌ 오류 발생")
            log.exception("Patch failed")
        return result

    def _fix_keys(self, check_backup=False):
        """게임에 빠진 키 이름 추가. 실패해도 번역 적용은 그대로 둠 (그 키만 번호로 나옴)."""
        try:
            if check_backup:
                # 번역을 다시 적용하지 않는 경로: 있는 백업만 확인 (없으면 새로 만들지 않음 — 지금 파일은 원본이 아님)
                if not (Path(self.game_path) / BACKUP_DIR_NAME).is_dir():
                    return
                create_backup(self.game_path)
            added = apply_key_aliases(self.game_path, self.state)
        except Exception as e:
            self._log(f"⚠️ 빠진 키 이름 추가 건너뜀: {e}")
            log.exception("Key alias patch failed")
            return
        if added:
            self._log(f"✅ 게임에 빠진 키 이름 추가: {', '.join(added)}")

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

def enable_dpi_awareness():
    """Windows 화면 배율을 패처가 직접 따르게 함.

    안 하면 Windows가 창을 그림처럼 늘려서 글씨가 흐려지고, 화면 크기도 배율만큼 작게 알려줌
    (1920x1200에 200%면 960x600). 그 화면보다 큰 창은 아래쪽 버튼이 화면 밖으로 나감.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 시스템 배율 (Tk 8.6은 모니터별 배율 변경을 처리하지 않음)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def load_ui_font():
    """패키지에 넣은 창 글꼴을 이 프로세스에서만 쓰게 등록 (PC에 설치하지 않음). 성공하면 True.

    app 폴더의 파일을 바로 등록하면 Windows가 그 파일을 잡고 있어서 패처 자체 업데이트가 app 폴더를 바꾸지 못함.
    그래서 사본을 따로 두고 그쪽을 등록.
    """
    source = Path(__file__).resolve().parent / UI_FONT_FILE
    base = os.environ.get("LOCALAPPDATA")
    if sys.platform != "win32" or not base or not source.is_file():
        return False
    import ctypes
    try:
        target = Path(base) / "LETransKr" / "ui" / UI_FONT_FILE
        if not (target.is_file() and sha256_file(target) == sha256_file(source)):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return bool(ctypes.windll.gdi32.AddFontResourceExW(str(target), 0x10, 0))  # FR_PRIVATE
    except OSError:
        return False


def ui_scale(root):
    """화면 배율 (100% = 1.0)."""
    return max(1.0, root.winfo_fpixels("1i") / 96)


def screen_work_area(root):
    """창을 둘 수 있는 화면 영역 (왼쪽, 위, 너비, 높이). 작업 표시줄 제외."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def window_frame(scale):
    """제목 표시줄과 테두리가 차지하는 (너비, 높이) 어림값."""
    return int(16 * scale), int(40 * scale)


def fit_window(area_w, area_h, scale):
    """화면 영역 안에 다 들어오는 창 (너비, 높이, 좁은 화면용 배치 여부)."""
    frame_w, frame_h = window_frame(scale)
    width = min(int(WINDOW_SIZE[0] * scale), area_w - frame_w)
    height = min(int(WINDOW_SIZE[1] * scale), area_h - frame_h)
    return width, height, height < int(COMPACT_BELOW * scale)


def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter import font as tkfont

    class PatcherApp:
        BG, BG2, FG, ACCENT, WARN, ENTRY_BG = "#0f0f1a", "#161628", "#e0e0e0", "#ff4d6a", "#ffa726", "#1c1c3a"

        def __init__(self, root, font_family=UI_FONT_FALLBACK):
            self.root = root
            self.font_family = font_family
            self.scale = ui_scale(root)
            self.root.title(f"Last Epoch 한국어 번역패치 v{PATCHER_VERSION}")
            # 화면(작업 표시줄 제외) 안에 다 들어오는 크기로 열고 가운데에 둠
            left, top, area_w, area_h = screen_work_area(root)
            width, height, self.compact = fit_window(area_w, area_h, self.scale)
            frame_w, frame_h = window_frame(self.scale)
            self.root.geometry(f"{width}x{height}+{left + max(0, (area_w - width - frame_w) // 2)}+{top + max(0, (area_h - height - frame_h) // 2)}")
            self.root.resizable(True, True)
            self.root.minsize(min(width, self.px(760)), min(height, self.px(440)))
            self.root.configure(bg=self.BG)
            self.game_path = tk.StringVar()
            self.do_translate = tk.BooleanVar(value=True)
            self.font_mode = tk.StringVar(value="none")
            self.font_file = tk.StringVar()
            self.font_all_text = tk.BooleanVar(value=True)
            self.status_text = tk.StringVar(value="대기 중...")
            self._busy = False
            self._releases = None  # None: 아직 못 받음 / []: 받기 실패
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
            self._build_ui(width)
            self._auto_detect()
            self._check_new_patch_bg()

        def px(self, value):
            """배율 100% 기준 픽셀 값을 지금 화면 배율에 맞춤 (글꼴은 pt라서 Tk가 알아서 키움)."""
            return int(round(value * self.scale))

        def f(self, size, bold=False):
            # 창 글꼴은 굵은 서체 하나만 넣었으므로 항상 bold로 요청 (가는 서체가 따로 설치된 PC에서 섞이지 않게)
            # 좁은 화면 배치에서는 한 단계 작게 (적용 버튼까지 한 화면에 들어오게)
            size += 1 if self.compact else 2
            return (self.font_family, size, "bold" if bold or self.font_family == UI_FONT_FAMILY else "normal")

        def _on_close(self):
            # 적용 도중 닫으면 게임 파일이 반쯤 바뀐 채 남고 잠금 파일도 지워지지 않음
            if self._busy:
                messagebox.showwarning("적용 중", "패치를 적용하는 중입니다. 끝날 때까지 기다려주세요.")
                return
            self.root.destroy()

        def _build_ui(self, width):
            px, f, compact = self.px, self.f, self.compact
            # 좁은 화면(compact): 위아래 여백을 줄이고 로그를 오른쪽 칸으로 옮겨 적용 버튼까지 다 보이게
            side, gap, edge = px(24), px(4 if compact else 10), px(8 if compact else 20)
            s = ttk.Style()
            s.theme_use("clam")
            s.configure("Title.TLabel", background=self.BG, foreground=self.ACCENT, font=f(14 if compact else 18, bold=True))
            s.configure("Sub.TLabel", background=self.BG, foreground="#888", font=f(10))
            s.configure("Status.TLabel", background=self.BG, foreground="#aaa", font=f(10))
            s.configure("Info.TLabel", background=self.BG2, foreground=self.FG, font=f(10))
            s.configure("Warn.TLabel", background=self.BG2, foreground=self.WARN, font=f(10))
            s.configure("Busy.TLabel", background=self.BG, foreground=self.WARN, font=f(11 if compact else 12, bold=True))
            s.configure("TButton", font=f(10))
            s.configure("TProgressbar", troughcolor=self.ENTRY_BG, background=self.ACCENT, thickness=px(12 if compact else 20))
            s.configure("Vertical.TScrollbar", arrowsize=px(12), width=px(12))
            option = dict(bg=self.BG, fg=self.FG, selectcolor=self.ENTRY_BG, activebackground=self.BG, activeforeground=self.FG, font=f(10))

            # 왼쪽: 패치 조작 / 오른쪽: 릴리즈 노트
            notes = tk.Frame(self.root, bg=self.BG)
            notes.pack(side="right", fill="both", expand=True, padx=(0, side), pady=(edge, edge))
            self.lbl_notes = ttk.Label(notes, text="업데이트 내용", style="Sub.TLabel")
            self.lbl_notes.pack(anchor="w")
            # 위: 릴리즈 목록 / 아래: 고른 릴리즈의 내용
            self._notes = []
            self.notes_list = tk.Listbox(notes, height=3 if compact else 5, bg=self.ENTRY_BG, fg=self.FG, font=f(10), relief="flat", bd=4, highlightthickness=0, activestyle="none", exportselection=False, selectbackground=self.ACCENT, selectforeground="#ffffff")
            self.notes_list.pack(fill="x", pady=(px(3), px(6)))
            self.notes_list.bind("<<ListboxSelect>>", lambda _: self._show_release())
            if compact:
                self.log_text = tk.Text(notes, height=4, width=10, bg=self.ENTRY_BG, fg="#7a7a9a", font=f(9), relief="flat", bd=5, state="disabled")
                self.log_text.pack(side="bottom", fill="x", pady=(px(6), 0))
            self.notes_text = tk.Text(notes, width=10, bg=self.BG2, fg=self.FG, font=f(10), relief="flat", bd=8, wrap="word", state="disabled", cursor="arrow", spacing1=2, spacing3=2)
            scroll = ttk.Scrollbar(notes, orient="vertical", command=self.notes_text.yview)
            self.notes_text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y", pady=(px(3), 0))
            self.notes_text.pack(fill="both", expand=True, pady=(px(3), 0))
            self.notes_text.tag_configure("version", foreground=self.ACCENT, font=f(12, bold=True))
            self.notes_text.tag_configure("date", foreground="#888", font=f(9))
            self.notes_text.tag_configure("bold", font=f(10, bold=True), foreground="#ffffff")
            self.notes_text.tag_configure("item", lmargin1=px(6), lmargin2=px(18))
            self.notes_text.tag_configure("subitem", lmargin1=px(22), lmargin2=px(34), foreground="#b0b0c0")
            self.notes_text.tag_configure("muted", foreground="#888")
            self._render_notes()

            left = tk.Frame(self.root, bg=self.BG, width=min(px(680), int(width * 0.62)))
            left.pack(side="left", fill="both")
            left.pack_propagate(False)

            top = tk.Frame(left, bg=self.BG)
            top.pack(fill="x", padx=side, pady=(edge, px(5)))
            title = ttk.Label(top, text="⚔  Last Epoch 한국어 번역패치", style="Title.TLabel")
            about = ttk.Label(top, text=f"v{PATCHER_VERSION}" if compact else f"github.com/{GITHUB_REPO}  ·  v{PATCHER_VERSION}", style="Sub.TLabel")
            if compact:
                title.pack(side="left")
                about.pack(side="left", padx=(px(10), 0), anchor="s")
            else:
                title.pack(anchor="w")
                about.pack(anchor="w")

            fp = tk.Frame(left, bg=self.BG)
            fp.pack(fill="x", padx=side, pady=(gap, px(5)))
            if not compact:
                ttk.Label(fp, text="게임 경로", style="Sub.TLabel").pack(anchor="w")
            fe = tk.Frame(fp, bg=self.BG)
            fe.pack(fill="x", pady=(px(3), 0))
            self.entry_path = tk.Entry(fe, textvariable=self.game_path, font=f(10), bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG, relief="flat", bd=5)
            self.entry_path.pack(side="left", fill="x", expand=True)
            ttk.Button(fe, text="게임 폴더 찾기" if compact else "찾기", command=self._browse).pack(side="right", padx=(px(5), 0))

            fi = tk.Frame(left, bg=self.BG2, bd=1, relief="solid")
            fi.pack(fill="x", padx=side, pady=(gap, px(5)))
            inner = px(2 if compact else 6)
            self.lbl_patch = ttk.Label(fi, text="  📦 패치 상태: 확인 중...", style="Info.TLabel")
            self.lbl_patch.pack(anchor="w", padx=px(8), pady=inner)
            self.lbl_game = ttk.Label(fi, text="", style="Info.TLabel")
            self.lbl_game.pack(anchor="w", padx=px(8), pady=(0, inner))

            fo = tk.Frame(left, bg=self.BG)
            fo.pack(fill="x", padx=side, pady=(gap, 0 if compact else px(5)))
            self.do_force = tk.BooleanVar(value=False)
            for text, var, command in [("번역 패치 적용 (끄면 게임 번역은 그대로 두고 폰트만 바꿈)", self.do_translate, self._render_notes),
                                       ("번역 강제 재적용 (같은 버전이어도)", self.do_force, None)]:
                tk.Checkbutton(fo, text=text, variable=var, command=command, pady=0, **option).pack(anchor="w")

            ff = tk.Frame(left, bg=self.BG)
            ff.pack(fill="x", padx=side, pady=(gap, 0))
            ttk.Label(ff, text="한국어 폰트", style="Sub.TLabel").pack(anchor="w")
            for mode, text in FONT_MODES.items():
                tk.Radiobutton(ff, text=text, variable=self.font_mode, value=mode, pady=0, **option).pack(anchor="w")
            ffe = tk.Frame(ff, bg=self.BG)
            ffe.pack(fill="x", padx=(px(22), 0))
            tk.Entry(ffe, textvariable=self.font_file, font=f(10), bg=self.ENTRY_BG, fg=self.FG, insertbackground=self.FG, relief="flat", bd=4).pack(side="left", fill="x", expand=True)
            ttk.Button(ffe, text="폰트 찾기", command=self._browse_font).pack(side="right", padx=(px(5), 0))
            tk.Checkbutton(ff, text="영문·숫자·기호도 선택한 폰트로 (끄면 한글만)", variable=self.font_all_text, pady=0, **option).pack(anchor="w", pady=(0 if compact else px(4), 0))

            # 적용·복원 버튼은 맨 아래에 먼저 자리를 잡음: 창이 작으면 버튼이 아니라 로그 칸이 줄어듦
            fb = tk.Frame(left, bg=self.BG)
            fb.pack(side="bottom", fill="x", padx=side, pady=(0, edge))
            self.btn_apply = ttk.Button(fb, text="🚀 패치 적용", command=self._start)
            self.btn_apply.pack(side="left", fill="x", expand=True, ipady=px(3 if compact else 8))
            self.btn_restore = ttk.Button(fb, text="↩ 복원 ▾", command=self._restore_menu)
            self.btn_restore.pack(side="right", padx=(px(10), 0), ipady=px(3 if compact else 8))

            fp2 = tk.Frame(left, bg=self.BG)
            fp2.pack(fill="x", padx=side, pady=(gap, px(5)))
            self.progress = ttk.Progressbar(fp2, mode="determinate", style="TProgressbar")
            self.progress.pack(fill="x")
            ttk.Label(fp2, textvariable=self.status_text, style="Status.TLabel").pack(anchor="w", pady=(px(3), 0))
            # 적용 중에만 글자가 채워짐 (진행바가 다 찬 걸 보고 끝난 줄 알고 닫는 일 방지)
            self.lbl_busy = ttk.Label(fp2, text="", style="Busy.TLabel")
            self.lbl_busy.pack(anchor="w", pady=(0 if compact else px(3), 0))

            if not compact:
                fl = tk.Frame(left, bg=self.BG)
                fl.pack(fill="both", expand=True, padx=side, pady=(px(5), px(10)))
                self.log_text = tk.Text(fl, height=3, bg=self.ENTRY_BG, fg="#7a7a9a", font=f(9), relief="flat", bd=5, state="disabled")
                self.log_text.pack(fill="both", expand=True)

        def _render_notes(self):
            """오른쪽 칸에 v1.0.0부터의 릴리즈 목록을 최신순으로 채우고, 고른 릴리즈의 내용(주요 작업)을 아래에 표시."""
            gp = self.game_path.get().strip()
            # 번역을 끈 상태에서는 "아직 적용 안 됨" 표시를 하지 않음 (패치 적용을 눌러도 번역은 안 바뀜)
            current = PatchState(gp).patch_version if gp and Path(gp).is_dir() and self.do_translate.get() else None
            selected = self._notes[self.notes_list.curselection()[0]]["tag"] if self._notes and self.notes_list.curselection() else None
            self._notes = release_history(self._releases, current) if self._releases else []
            self.notes_list.delete(0, "end")
            if not self._notes:
                self.lbl_notes.configure(text="업데이트 내용")
                message = "릴리즈 노트를 불러오는 중..." if self._releases is None else "릴리즈 노트를 불러오지 못했습니다."
                self._show_release(message)
                return
            pending = self._notes[0]["new"]
            self.lbl_notes.configure(text=f"새로 적용될 내용 ({current} → {self._notes[0]['tag']})" if pending else "업데이트 내용")
            for i, entry in enumerate(self._notes):
                self.notes_list.insert("end", ("● " if entry["new"] else "   ") + entry["title"])
                if entry["new"]:
                    self.notes_list.itemconfigure(i, foreground=self.WARN)
            # 고른 것이 있으면 유지, 없으면 최신 릴리즈
            index = next((i for i, entry in enumerate(self._notes) if entry["tag"] == selected), 0)
            self.notes_list.selection_set(index)
            self.notes_list.see(index)
            self._show_release()

        def _show_release(self, message=None):
            """목록에서 고른 릴리즈의 내용을 표시. message가 있으면 그 안내문만."""
            box = self.notes_text
            box.configure(state="normal")
            box.delete("1.0", "end")
            picked = self.notes_list.curselection()
            if message or not picked:
                box.insert("end", message or "", "muted")
                box.configure(state="disabled")
                return
            entry = self._notes[picked[0]]
            box.insert("end", entry["title"] + "\n", "version")
            box.insert("end", entry["date"] + ("  ·  아직 적용 안 됨 (패치 적용을 누르면 적용)" if entry["new"] else "") + "\n", "date")
            for line in entry["lines"]:
                if not line.strip():
                    continue
                lead, spans = markdown_spans(line)
                tag = ("subitem" if line.startswith(" ") else "item") if lead else None
                box.insert("end", lead, tag)
                for text, bold in spans:
                    box.insert("end", text, tuple(t for t in (tag, "bold" if bold else None) if t))
                box.insert("end", "\n", tag)
            box.configure(state="disabled")

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
                self._stop_working()
                self.progress["value"] = cur / tot * 100
            self.root.update_idletasks()

        def _work(self):
            """진행률을 알 수 없는 단계: 진행바를 채우지 않고 좌우로 움직여서 작업 중임을 표시."""
            if str(self.progress["mode"]) != "indeterminate":
                self.progress.configure(mode="indeterminate", value=0)
                self.progress.start(12)

        def _stop_working(self):
            if str(self.progress["mode"]) == "indeterminate":
                self.progress.stop()
                self.progress.configure(mode="determinate", value=0)

        def _tick_busy(self, since):
            if not self._busy:
                self.lbl_busy.configure(text="")
                return
            sec = int(time.monotonic() - since)
            self.lbl_busy.configure(text=f"⏳ 작업 중 — 완료 창이 뜰 때까지 닫지 마세요 (경과 {sec // 60}:{sec % 60:02d})")
            self.root.after(1000, self._tick_busy, since)

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
                if bid and state.translate and state.game_was_updated(bid):
                    self.lbl_game.configure(text=f"  ⚠️ 게임 업데이트 감지! 재적용 권장 (build {bid})", style="Warn.TLabel")
                else:
                    self.lbl_game.configure(text=f"  🎮 게임 빌드: {bid or '?'}")
            else:
                self.lbl_patch.configure(text="  📦 패치 미적용" if state.translate else "  📦 번역 패치 사용 안 함 (게임 번역 그대로)")
                self.lbl_game.configure(text=f"  🎮 게임 빌드: {bid or '?'}")
            self.do_translate.set(state.translate)
            font = state.data.get("font") or {}
            self.font_mode.set(font.get("mode") if font.get("mode") in FONT_MODES else "none")
            if font.get("ttf"):
                self.font_file.set(font["ttf"])
            self.font_all_text.set(font.get("all_text", True))
            self._render_notes()

        def _browse(self):
            p = filedialog.askdirectory(title="Last Epoch 폴더")
            if p:
                self.game_path.set(p)
                self._refresh_info(p)

        def _browse_font(self):
            p = filedialog.askopenfilename(title="폰트 파일", filetypes=[("폰트 파일", "*.ttf *.otf")])
            if p:
                self.font_file.set(p)
                self.font_mode.set("custom")

        def _check_new_patch_bg(self):
            """백그라운드에서 새 번역 업데이트 확인 + 릴리즈 노트 받기."""
            def show_notes(releases):
                self._releases = releases
                self._render_notes()

            def check():
                try:
                    releases = github_api_get(GITHUB_API_RELEASE_LIST)
                except Exception:
                    releases = []
                self.root.after(0, show_notes, releases)
                update = find_patcher_update(releases)
                if update:
                    self.root.after(0, self._offer_patcher_update, update)
                try:
                    gp = self.game_path.get().strip()
                    if not gp:
                        return
                    state = PatchState(gp)
                    release = fetch_latest_release()
                    tag = release.get("tag_name", "")
                    if state.translate and state.is_outdated(tag):
                        self.root.after(0, lambda: self._notify_new_patch(tag, state.patch_version))
                except Exception:
                    pass
            threading.Thread(target=check, daemon=True).start()

        def _offer_patcher_update(self, update):
            """새 패처가 있으면 물어보고 받아서 교체한 뒤 다시 시작."""
            self._log(f"🔔 새 패처 v{update['version']} (현재 v{PATCHER_VERSION})")
            if self._busy:
                return
            if not package_dir():
                self._log(f"   받기: {update['page']}")
                return
            if not messagebox.askyesno("패처 업데이트", f"새 패처 v{update['version']}이 있습니다. (현재 v{PATCHER_VERSION})\n\n지금 업데이트하고 다시 시작할까요?"):
                return
            self._busy = True
            self.btn_apply.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self._status("패처 업데이트 중...")

            def work():
                try:
                    script = apply_patcher_update(update, lambda c, t: self.root.after(0, self._prog, c, t))
                except Exception as exc:
                    self.root.after(0, failed, str(exc))
                    return
                self.root.after(0, restart, script)

            def restart(script):
                restart_patcher(script)
                self.root.destroy()

            def failed(reason):
                self._busy = False
                self.btn_apply.configure(state="normal")
                self.btn_restore.configure(state="normal")
                self._log(f"❌ 패처 업데이트 실패: {reason}")
                self._status("패처 업데이트 실패 — 지금 버전으로 계속 사용할 수 있습니다.")
                messagebox.showerror("패처 업데이트 실패", f"{reason}\n\n새 패키지 받기:\n{update['page']}")

            threading.Thread(target=work, daemon=True).start()

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
            try:
                font = normalize_font({"mode": self.font_mode.get(), "ttf": self.font_file.get().strip(), "all_text": self.font_all_text.get()})
            except ValueError as exc:
                messagebox.showerror("오류", str(exc))
                return
            self.btn_apply.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self.progress["value"] = 0
            self._busy = True
            self.btn_apply.configure(text="⏳ 적용 중...")
            self._work()
            self._tick_busy(time.monotonic())
            threading.Thread(target=self._run_patch, args=(gp, self.do_force.get(), font, self.do_translate.get()), daemon=True).start()

        def _run_patch(self, gp, force=False, font=None, translate=True):
            orch = PatchOrchestrator(gp, log_cb=lambda m: self.root.after(0, self._log, m), status_cb=lambda m: self.root.after(0, self._status, m), progress_cb=lambda c, t: self.root.after(0, self._prog, c, t), font=font, working_cb=lambda: self.root.after(0, self._work), translate=translate)
            if force and translate:
                orch.state.data.pop("patch_version", None)
            res = orch.run()
            def done():
                self._busy = False
                self._stop_working()
                self.lbl_busy.configure(text="")
                self.btn_apply.configure(state="normal", text="🚀 패치 적용")
                self.btn_restore.configure(state="normal")
                self._refresh_info(gp)
                # 다른 창을 보고 있어도 끝난 걸 알 수 있게
                self.root.deiconify()
                self.root.lift()
                self.root.bell()
                if res["success"]:
                    self.progress["value"] = 100
                    messagebox.showinfo("완료", res["message"])
                else:
                    messagebox.showerror("오류", res["message"])
            self.root.after(0, done)

        def _restore_menu(self):
            """복원 버튼 아래에 무엇을 되돌릴지 고르는 메뉴를 띄움."""
            menu = tk.Menu(self.root, tearoff=0, bg=self.BG2, fg=self.FG, activebackground=self.ACCENT, activeforeground="#ffffff", font=self.f(10))
            for what, label in RESTORE_PARTS.items():
                menu.add_command(label=f"{label} 복원", command=lambda w=what: self._restore(w))
            menu.tk_popup(self.btn_restore.winfo_rootx(), self.btn_restore.winfo_rooty() + self.btn_restore.winfo_height())

        def _restore(self, what="all"):
            gp = self.game_path.get().strip()
            if not gp:
                messagebox.showerror("오류", "경로를 지정해주세요.")
                return
            detail = {"all": "번역과 폰트 모두 원본으로 되돌립니다",
                      "translation": "번역만 원본으로 되돌리고 폰트는 그대로 둡니다.\n'번역 패치 적용' 체크도 꺼집니다",
                      "font": "폰트만 게임 기본으로 되돌리고 번역은 그대로 둡니다"}[what]
            if not messagebox.askyesno("확인", f"백업에서 복원하시겠습니까?\n({detail})"):
                return
            # 폰트 복원은 큰 파일을 되돌리느라 몇 초 걸림: 적용 때처럼 작업 중임을 보여주고 창이 굳지 않게 따로 돌림
            self._busy = True
            self.btn_apply.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self._status(f"{RESTORE_PARTS[what]} 복원 중...")
            self._work()
            self._tick_busy(time.monotonic())

            def work():
                try:
                    outcome = restore_part(gp, what), None
                except Exception as exc:
                    log.exception("Restore failed")
                    outcome = False, explain_error(exc)
                self.root.after(0, done, *outcome)

            def done(restored, error):
                self._busy = False
                self._stop_working()
                self.lbl_busy.configure(text="")
                self.btn_apply.configure(state="normal")
                self.btn_restore.configure(state="normal")
                self._refresh_info(gp)
                if error:
                    self._log(f"❌ 복원 실패: {error}")
                    self._status("❌ 복원 실패")
                    messagebox.showerror("복원 실패", error)
                elif restored:
                    self._log(f"✅ 복원 완료 ({RESTORE_PARTS[what]})")
                    self._status("✅ 복원 완료")
                    messagebox.showinfo("완료", "복원 완료!")
                elif what == "font":
                    self._status("폰트는 이미 게임 기본 상태입니다.")
                    messagebox.showinfo("안내", "폰트는 이미 게임 기본 상태입니다.")
                else:
                    self._status("백업을 찾을 수 없습니다.")
                    messagebox.showerror("오류", "백업을 찾을 수 없습니다.")

            threading.Thread(target=work, daemon=True).start()

    if sys.platform == "win32":
        try:
            # 작업 표시줄에 pythonw가 아니라 패처 아이콘으로 표시
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("fnrkp089.LETransKr.Patcher")
        except Exception:
            pass
    enable_dpi_awareness()
    has_font = load_ui_font()
    root = tk.Tk()
    family = UI_FONT_FAMILY if has_font and UI_FONT_FAMILY in tkfont.families(root) else UI_FONT_FALLBACK
    icon = Path(__file__).resolve().parent / APP_ICON
    if icon.is_file():
        try:
            root.iconbitmap(default=str(icon))
        except Exception:
            pass
    PatcherApp(root, family)
    root.mainloop()


# ━━━ 10. CLI ━━━

def run_cli():
    import argparse
    parser = argparse.ArgumentParser(description="Last Epoch 한국어 번역패치")
    parser.add_argument("--cli", action="store_true", help="CLI 모드")
    parser.add_argument("--path", help="게임 폴더 경로")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--restore", nargs="?", const="all", choices=list(RESTORE_PARTS), help="원본으로 복원 (생략 시 all)")
    parser.add_argument("--font-only", action="store_true", help="번역은 건드리지 않고 폰트만 적용 (--font 필요)")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--font", choices=list(FONT_MODES), help="한국어 폰트 (생략 시 폰트는 건드리지 않음)")
    parser.add_argument("--font-file", help="--font custom에 쓸 .ttf/.otf 경로")
    parser.add_argument("--font-korean-only", action="store_true", help="영문·숫자·기호는 게임 원래 폰트로 둠")
    args = parser.parse_args()
    if args.font_file and not args.font:
        args.font = "custom"
    if args.font_only and not args.font:
        parser.error("--font-only에는 --font가 필요합니다")

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
            print("  패치 미적용" if st.translate else "  번역 패치 사용 안 함")
        print(f"  폰트: {FONT_MODES.get((st.data.get('font') or {}).get('mode'), FONT_MODES['none'])}")
        return

    if args.restore:
        print(f"✅ 복원 완료! ({RESTORE_PARTS[args.restore]})" if restore_part(gp, args.restore) else "❌ 되돌릴 것이 없음")
        return

    font = None
    if args.font:
        try:
            font = normalize_font({"mode": args.font, "ttf": args.font_file, "all_text": not args.font_korean_only})
        except ValueError as exc:
            print(f"오류: {exc}")
            sys.exit(1)

    def cli_prog(dl, tot):
        if tot > 0:
            pct = dl / tot * 100
            bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
            print(f"\r  [{bar}] {pct:.0f}%", end="", flush=True)

    orch = PatchOrchestrator(gp, log_cb=lambda m: print(f"  {m}"), status_cb=lambda m: print(f"\n  >> {m}"), progress_cb=cli_prog, font=font, translate=not args.font_only)
    if args.force and not args.font_only:
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
