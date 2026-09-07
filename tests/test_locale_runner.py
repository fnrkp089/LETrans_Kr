from pathlib import Path
import contextlib
import io
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'patcher'))
import locale_runner
import workbench_common as common

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def put(self, path, data):
        common.write_json(self.root / path, data)
    def get(self, path):
        return common.read_json(self.root / path)

    def test_zero_exit_error_output_is_rejected(self):
        exe=self.root/'dummy.exe';exe.touch();bundle=self.root/'game.bundle';bundle.touch()
        with patch.object(locale_runner.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='Error\nEnter to exit',stderr='failed')):
            with self.assertRaises(RuntimeError):locale_runner.run_checked(exe,bundle,'export',self.root/'out')

    def test_failed_export_preserves_existing_dump(self):
        self.put('dump/Skills_ko.json',{'1':'old'})
        with patch.object(locale_runner,'run_checked',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):locale_runner.export_locale('exe','bundle',self.root/'dump','ko')
        self.assertEqual(self.get('dump/Skills_ko.json'),{'1':'old'})

    def test_successful_export_replaces_stale_files_with_backup(self):
        self.put('dump/Old_ko.json',{'1':'old'})
        def fake(exe,bundle,action,target):common.write_json(Path(target)/'New_ko.json',{'2':'new'})
        with patch.object(locale_runner,'run_checked',side_effect=fake):
            locale_runner.export_locale('exe','bundle',self.root/'dump','ko')
        self.assertFalse((self.root/'dump/Old_ko.json').exists())
        self.assertTrue(list((self.root/'backups').glob('*/dump/Old_ko.json')))

    def test_import_is_staged_verified_and_backed_up(self):
        bundle=self.root/'aa/StandaloneWindows64/ko.bundle';bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b'original');catalog=self.root/'aa/catalog.bin';catalog.write_bytes(b'catalog')
        self.put('source/Skills_ko.json',{'1':'번역'})
        def fake(exe,stage,action,target):
            self.assertNotEqual(stage,bundle)
            self.assertEqual(bundle.read_bytes(),b'original')
            if action=='import':
                stage.write_bytes(b'patched');(stage.parent.parent/'catalog.bin').write_bytes(b'patchedcat')
            else:common.write_json(Path(target)/'Skills_ko.json',{'1':'번역'})
        with patch.object(locale_runner,'run_checked',side_effect=fake):
            count,backup=locale_runner.import_locale('exe',bundle,self.root/'source')
        self.assertEqual(count,1);self.assertEqual(bundle.read_bytes(),b'patched')
        self.assertEqual((backup/'ko.bundle').read_bytes(),b'original')
        self.assertEqual((backup/'catalog.bin').read_bytes(),b'catalog')

    def test_import_bad_roundtrip_never_replaces_game(self):
        bundle=self.root/'aa/StandaloneWindows64/ko.bundle';bundle.parent.mkdir(parents=True)
        bundle.write_bytes(b'original');(self.root/'aa/catalog.bin').write_bytes(b'catalog')
        self.put('source/Skills_ko.json',{'1':'번역'})
        def fake(exe,stage,action,target):
            if action=='export':common.write_json(Path(target)/'Skills_ko.json',{'1':'wrong'})
        with patch.object(locale_runner,'run_checked',side_effect=fake):
            with self.assertRaises(ValueError):locale_runner.import_locale('exe',bundle,self.root/'source')
        self.assertEqual(bundle.read_bytes(),b'original')
