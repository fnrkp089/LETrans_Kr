import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'patcher') if (ROOT / 'patcher').is_dir() else str(ROOT))
import patcher


def release(tag, assets=(), **extra):
    return {"tag_name": tag, "html_url": f"https://example.com/{tag}",
            "assets": [{"name": n, "browser_download_url": f"https://example.com/{tag}/{n}"} for n in assets], **extra}


class FindPatcherUpdateTest(unittest.TestCase):
    def test_picks_highest_newer_patcher_release(self):
        releases = [release("v9.9.9"), release("patcher-v0.8.1", ["LastEpoch_KR_Patcher-app-v0.8.1.zip", "SHA256SUMS"]),
                    release("patcher-v0.9.0", ["LastEpoch_KR_Patcher-app-v0.9.0.zip", "SHA256SUMS"]),
                    release("patcher-v0.8.0"), release("patcher-v1.0.0", draft=True), release("patcher-v1.1.0", prerelease=True)]
        update = patcher.find_patcher_update(releases, current="0.8.0")
        self.assertEqual(update["version"], "0.9.0")
        self.assertEqual(update["url"], "https://example.com/patcher-v0.9.0/LastEpoch_KR_Patcher-app-v0.9.0.zip")
        self.assertEqual(update["checksums"], "https://example.com/patcher-v0.9.0/SHA256SUMS")

    def test_none_when_current_is_latest(self):
        self.assertIsNone(patcher.find_patcher_update([release("patcher-v0.8.0"), release("v1.0.4")], current="0.8.0"))
        self.assertIsNone(patcher.find_patcher_update([], current="0.8.0"))
        self.assertIsNone(patcher.find_patcher_update(None, current="0.8.0"))


class ApplyPatcherUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.app = self.tmp / "pkg" / "app"
        self.app.mkdir(parents=True)
        (self.tmp / "pkg" / "runtime").mkdir()
        (self.app / "patcher.py").write_text("old", encoding="utf-8")
        (self.app / "removed.py").write_text("old", encoding="utf-8")
        (self.app / "package.json").write_text(json.dumps({"patcher": "0.8.0", "python": "3.14.7"}), encoding="utf-8")
        self.archive = self.make_zip("0.9.0", "3.14.7")
        self.update = {"version": "0.9.0", "name": "app.zip", "url": "u", "checksums": "c", "page": "p"}

    def make_zip(self, version, python):
        archive = self.tmp / f"src-{version}-{python}.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("patcher.py", "new")
            z.writestr("package.json", json.dumps({"patcher": version, "python": python}))
        return archive

    def run_update(self, archive=None, digest=None, smoke="0.9.0\n", code=0):
        archive = archive or self.archive
        digest = digest or hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(patcher, "package_dir", return_value=self.app), \
             patch.object(patcher, "download_and_parse_checksums", return_value={"app.zip": digest}), \
             patch.object(patcher, "download_file", side_effect=lambda url, dest, cb=None: shutil.copyfile(archive, dest)), \
             patch.object(patcher.subprocess, "run", return_value=SimpleNamespace(returncode=code, stdout=smoke)):
            return patcher.apply_patcher_update(self.update)

    def assert_untouched(self):
        self.assertEqual((self.app / "patcher.py").read_text(encoding="utf-8"), "old")
        self.assertEqual(sorted(p.name for p in (self.tmp / "pkg").iterdir()), ["app", "runtime"])

    def test_replaces_app_folder(self):
        self.assertEqual(self.run_update(), self.app / "patcher.py")
        self.assertEqual((self.app / "patcher.py").read_text(encoding="utf-8"), "new")
        self.assertEqual(sorted(p.name for p in self.app.iterdir()), ["package.json", "patcher.py"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "pkg").iterdir()), ["app", "runtime"])

    def test_checksum_mismatch_keeps_current(self):
        with self.assertRaisesRegex(RuntimeError, "체크섬"):
            self.run_update(digest="0" * 64)
        self.assert_untouched()

    def test_different_python_runtime_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "런타임"):
            self.run_update(archive=self.make_zip("0.9.0", "3.15.0"))
        self.assert_untouched()

    def test_version_mismatch_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "버전"):
            self.run_update(archive=self.make_zip("0.9.1", "3.14.7"))
        self.assert_untouched()

    def test_failed_start_check_keeps_current(self):
        with self.assertRaisesRegex(RuntimeError, "실행 확인"):
            self.run_update(smoke="", code=1)
        self.assert_untouched()

    def test_source_run_is_refused(self):
        with patch.object(patcher, "package_dir", return_value=None), self.assertRaises(RuntimeError):
            patcher.apply_patcher_update(self.update)


if __name__ == "__main__":
    unittest.main()
