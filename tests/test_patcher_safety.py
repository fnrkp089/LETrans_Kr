"""Patcher tests run without game files, network requests, or external executables."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'patcher') if (ROOT / 'patcher').is_dir() else str(ROOT))
import patcher


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name)
        self.bundle = self.game / patcher.BUNDLE_SUBDIR / patcher.BUNDLE_FILENAME
        self.bundle.parent.mkdir(parents=True)
        self.catalog = self.game / patcher.CATALOG_RELPATH
        self.bundle.write_bytes(b'original bundle')
        self.catalog.write_bytes(b'original catalog')
        self.build = patch.object(patcher, 'get_steam_buildid', return_value='100')
        self.build.start()

    def tearDown(self):
        self.build.stop()
        self.tmp.cleanup()

    def test_repeated_patch_keeps_original_and_restores_pair(self):
        backup = Path(patcher.create_backup(self.game))
        self.bundle.write_bytes(b'patched')
        self.catalog.write_bytes(b'patched catalog')
        patcher.create_backup(self.game)
        self.assertEqual((backup / self.bundle.name).read_bytes(), b'original bundle')
        self.assertTrue(patcher.restore_backup(self.game))
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')
        self.assertEqual(self.catalog.read_bytes(), b'original catalog')

    def test_partial_or_corrupt_backup_rejected_before_restore(self):
        backup = Path(patcher.create_backup(self.game))
        self.bundle.write_bytes(b'patched')
        (backup / self.catalog.name).write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):
            patcher.restore_backup(self.game)
        self.assertEqual(self.bundle.read_bytes(), b'patched')
        (backup / self.catalog.name).unlink()
        with self.assertRaises(RuntimeError):
            patcher.restore_backup(self.game)
        self.assertEqual(self.bundle.read_bytes(), b'patched')

    def test_old_build_refused_and_archived_on_new_patch(self):
        patcher.create_backup(self.game)
        self.bundle.write_bytes(b'new game')
        with patch.object(patcher, 'get_steam_buildid', return_value='101'):
            with self.assertRaises(RuntimeError):
                patcher.restore_backup(self.game)
            patcher.create_backup(self.game)
        self.assertTrue(list(self.game.glob(patcher.BACKUP_DIR_NAME + '_*')))
        self.assertEqual((self.game / patcher.BACKUP_DIR_NAME / self.bundle.name).read_bytes(), b'new game')

    def test_ordinary_restore_failure_rolls_back_both_files(self):
        patcher.create_backup(self.game)
        self.bundle.write_bytes(b'patched bundle')
        self.catalog.write_bytes(b'patched catalog')
        real_write = patcher.atomic_write
        calls = 0
        def fail_once(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated write failure')
            real_write(path, data)
        with patch.object(patcher, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                patcher.restore_backup(self.game)
        self.assertEqual(self.bundle.read_bytes(), b'patched bundle')
        self.assertEqual(self.catalog.read_bytes(), b'patched catalog')

    def test_alternative_catalog_and_bundle_names(self):
        self.bundle = self.bundle.rename(self.bundle.with_name('custom-korean.bundle'))
        self.catalog = self.catalog.rename(self.catalog.with_name('catalog.json'))
        patcher.create_backup(self.game)
        self.bundle.write_bytes(b'patched')
        patcher.restore_backup(self.game)
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')


class ArchiveTests(unittest.TestCase):
    def test_windows_paths_symlink_and_case_duplicates_rejected(self):
        raw = zipfile.ZipInfo('raw'); raw.filename = 'folder\\bad.json'
        cases = [[raw], ['../bad'], ['CON.json'], ['dir/file.'], ['a.json', 'A.json']]
        link = zipfile.ZipInfo('link.json'); link.create_system = 3; link.external_attr = 0o120777 << 16
        cases.append([link])
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'bad.zip'
                with zipfile.ZipFile(archive, 'w') as zf:
                    for item in entries:
                        zf.writestr(item, 'bad')
                with self.assertRaises(ValueError):
                    patcher.extract_checked(archive, Path(tmp) / 'output')


if __name__ == '__main__':
    unittest.main()
