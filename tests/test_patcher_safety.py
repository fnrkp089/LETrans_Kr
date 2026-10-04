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

    def test_backup_permission_error_tells_how_to_fix(self):
        denied = PermissionError(13, 'Permission denied', str(self.game / patcher.BACKUP_DIR_NAME / 'backup_state.json'))
        self.assertIn('관리자 권한으로 실행', patcher.explain_error(denied))
        self.assertNotIn('관리자 권한', patcher.explain_error(PermissionError(13, 'Permission denied', str(self.bundle))))
        self.assertEqual(patcher.explain_error(RuntimeError('다른 오류')), '다른 오류')

    def test_staging_folder_is_removed_and_backup_left_in_game_folder(self):
        patcher.create_backup(self.game)
        self.assertEqual(sorted(p.name for p in self.game.iterdir() if p.name.startswith('.')), [])
        self.assertTrue((self.game / patcher.BACKUP_DIR_NAME / 'backup_state.json').is_file())

    def test_alternative_catalog_and_bundle_names(self):
        self.bundle = self.bundle.rename(self.bundle.with_name('custom-korean.bundle'))
        self.catalog = self.catalog.rename(self.catalog.with_name('catalog.json'))
        patcher.create_backup(self.game)
        self.bundle.write_bytes(b'patched')
        patcher.restore_backup(self.game)
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')


class FontTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name) / 'game'
        self.resources = self.game / patcher.RESOURCES_RELPATH
        self.resources.parent.mkdir(parents=True)
        self.resources.write_bytes(b'original assets')
        self.bundle = self.game / patcher.FONT_BUNDLE_RELPATH
        self.bundle.parent.mkdir(parents=True)
        self.bundle.write_bytes(b'original bundle')
        self.tool = Path(self.tmp.name) / patcher.FONT_TOOL_NAME
        self.tool.write_bytes(b'')
        self.ttf = Path(self.tmp.name) / 'My Font.ttf'
        self.ttf.write_bytes(b'font one')
        self.state = patcher.PatchState(self.game)
        self.buildid = '100'
        self.build = patch.object(patcher, 'get_steam_buildid', side_effect=lambda game: self.buildid)
        self.build.start()
        self.packages = []
        self.run_tool = patch.object(patcher, 'run_font_tool', side_effect=self.fake_tool)
        self.run_tool.start()

    def tearDown(self):
        self.run_tool.stop()
        self.build.stop()
        self.tmp.cleanup()

    def fake_tool(self, tool_exe, game, package, bundles=0):
        manifest = patcher.json.loads((Path(package) / 'manifest.json').read_text(encoding='utf-8'))
        self.packages.append(manifest)
        source = manifest['dynamicFonts'][patcher.KR_FONT_ASSETS[0]]
        marker = (Path(package) / source['ttf']).read_bytes() if 'ttf' in source else source['fontAsset'].encode()
        self.resources.write_bytes(self.resources.read_bytes() + b'+' + marker)
        for bundle in manifest['dynamicFontBundles']:  # relative to Last Epoch_Data, as the tool resolves them
            path = self.resources.parent / bundle
            path.write_bytes(path.read_bytes() + b'+' + marker)
        return ['Made dynamic'] * len(patcher.KR_FONT_ASSETS) * (1 + bundles)

    def apply(self, mode, ttf=None):
        return patcher.apply_font(self.game, self.state, {'mode': mode, 'ttf': ttf}, self.tool)

    def test_switching_fonts_always_starts_from_original(self):
        self.apply('bold')
        self.assertEqual(self.resources.read_bytes(), b'original assets+Pretendard-Bold')
        self.assertEqual(set(self.packages[0]['dynamicFonts']), set(patcher.KR_FONT_ASSETS))
        self.assertEqual(self.bundle.read_bytes(), b'original bundle+Pretendard-Bold')
        self.assertEqual(self.packages[0]['dynamicFontBundles'], ['StreamingAssets/LEAssetBundles/PermaLoad.bundle'])
        self.apply('custom', self.ttf)
        self.assertEqual(self.resources.read_bytes(), b'original assets+font one')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle+font one')
        self.assertTrue(patcher.font_is_applied(self.game, self.state, {'mode': 'custom', 'ttf': self.ttf}))
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'bold'}))
        self.ttf.write_bytes(b'font two')  # same path, different file
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'custom', 'ttf': self.ttf}))

    def test_all_text_follows_font_unless_turned_off(self):
        self.apply('bold')
        self.assertEqual(self.packages[0]['dynamicFontCharacters'], patcher.FONT_ALL_CHARACTERS)
        self.assertTrue(patcher.font_is_applied(self.game, self.state, {'mode': 'bold'}))
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'bold', 'all_text': False}))
        patcher.apply_font(self.game, self.state, {'mode': 'bold', 'all_text': False}, self.tool)
        self.assertNotIn('dynamicFontCharacters', self.packages[1])
        self.assertTrue(patcher.font_is_applied(self.game, self.state, {'mode': 'bold', 'all_text': False}))

    def test_restore_returns_original_and_clears_state(self):
        self.assertFalse(patcher.restore_font(self.game))
        self.apply('bold')
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'none'}))
        self.assertTrue(patcher.restore_font(self.game))
        self.assertEqual(self.resources.read_bytes(), b'original assets')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')
        self.assertNotIn('font', patcher.PatchState(self.game).data)
        self.assertTrue(patcher.font_is_applied(self.game, patcher.PatchState(self.game), {'mode': 'none'}))

    def test_game_update_replacing_file_becomes_new_original(self):
        self.apply('bold')
        self.buildid = '101'
        self.resources.write_bytes(b'updated assets')  # Steam은 바뀐 파일만 교체: 번들은 패치된 채로 남음
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'bold'}))
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'none'}))
        self.apply('bold')
        self.assertEqual(self.resources.read_bytes(), b'updated assets+Pretendard-Bold')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle+Pretendard-Bold')
        self.assertTrue(patcher.restore_font(self.game))
        self.assertEqual(self.resources.read_bytes(), b'updated assets')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')

    def test_resources_only_patch_of_v070_is_upgraded(self):
        # v0.7.0은 resources.assets만 패치하고 상태/백업에 파일 하나만 기록했음
        backup = self.game / patcher.FONT_BACKUP_DIR_NAME
        backup.mkdir()
        (backup / self.resources.name).write_bytes(b'original assets')
        patcher.write_json(backup / 'backup_state.json', {'buildid': '100', 'path': str(patcher.RESOURCES_RELPATH),
            'sha256': patcher.sha256_file(self.resources)})
        self.resources.write_bytes(b'original assets+old')
        self.state.data['font'] = {'mode': 'bold', 'patched_sha256': patcher.sha256_file(self.resources)}
        self.state.save()
        self.assertFalse(patcher.font_is_applied(self.game, self.state, {'mode': 'bold'}))
        self.apply('bold')
        self.assertEqual(self.resources.read_bytes(), b'original assets+Pretendard-Bold')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle+Pretendard-Bold')
        self.assertTrue(patcher.restore_font(self.game))
        self.assertEqual(self.resources.read_bytes(), b'original assets')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')

    def test_game_without_font_bundle_patches_resources_only(self):
        self.bundle.unlink()
        self.apply('bold')
        self.assertEqual(self.packages[0]['dynamicFontBundles'], [])
        self.assertEqual(self.resources.read_bytes(), b'original assets+Pretendard-Bold')
        self.assertTrue(patcher.font_is_applied(self.game, self.state, {'mode': 'bold'}))

    def test_game_update_keeping_patched_file_keeps_old_original(self):
        self.apply('bold')
        self.buildid = '101'  # Steam은 바뀌지 않은 파일을 건드리지 않음
        self.apply('custom', self.ttf)
        self.assertEqual(self.resources.read_bytes(), b'original assets+font one')

    def test_lost_state_on_same_build_does_not_back_up_patched_file(self):
        self.apply('bold')
        (self.game / patcher.PATCH_STATE_FILE).unlink()
        self.state = patcher.PatchState(self.game)
        self.apply('custom', self.ttf)
        self.assertEqual(self.resources.read_bytes(), b'original assets+font one')

    def test_tool_failure_leaves_original_and_no_state(self):
        def broken(tool_exe, game, package, bundles=0):
            self.resources.write_bytes(b'patched before the bundle failed')
            raise RuntimeError('LEFontPatch 실패')
        self.apply('bold')
        with patch.object(patcher, 'run_font_tool', side_effect=broken):
            with self.assertRaises(RuntimeError):
                self.apply('custom', self.ttf)
        self.assertEqual(self.resources.read_bytes(), b'original assets')
        self.assertEqual(self.bundle.read_bytes(), b'original bundle')
        self.assertNotIn('font', patcher.PatchState(self.game).data)

    def test_corrupt_backup_refused(self):
        self.apply('bold')
        (self.game / patcher.FONT_BACKUP_DIR_NAME / self.resources.name).write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):
            self.apply('none')
        self.assertEqual(self.resources.read_bytes(), b'original assets+Pretendard-Bold')

    def test_invalid_choices_rejected_before_touching_game(self):
        for font in [{'mode': 'comic'}, {'mode': 'custom'}, {'mode': 'custom', 'ttf': self.tool}]:
            with self.subTest(font=font), self.assertRaises(ValueError):
                patcher.apply_font(self.game, self.state, font, self.tool)
        with self.assertRaises(FileNotFoundError):
            patcher.apply_font(self.game, self.state, {'mode': 'bold'}, self.tool.with_name('missing.exe'))
        self.assertEqual(self.resources.read_bytes(), b'original assets')
        self.assertFalse((self.game / patcher.FONT_BACKUP_DIR_NAME).exists())

    def test_tool_failure_message_says_what_happened(self):
        self.run_tool.stop()
        try:
            local = Path(self.tmp.name) / 'local'
            cases = [
                (2, 'Reading TMP_FontAssets\nWarning: Font not found: x\nNo font replaced, cancelling . . .', '', '폰트를 찾지 못했습니다'),
                (1, 'Reading TMP_FontAssets\nError\n\nEnter to exit . . .', 'System.IO.IOException: locked\n   at X', 'System.IO.IOException: locked'),
                (3221225477, 'Reading TMP_FontAssets\nMaking fonts dynamic:', '', '마지막 단계: Making fonts dynamic:'),
            ]
            for code, stdout, stderr, expected in cases:
                completed = patcher.subprocess.CompletedProcess([], code, stdout, stderr)
                with self.subTest(code=code), patch.dict(patcher.os.environ, {'LOCALAPPDATA': str(local)}), \
                        patch.object(patcher.subprocess, 'run', return_value=completed):
                    with self.assertRaises(RuntimeError) as raised:
                        patcher.run_font_tool(self.tool, self.game, self.tmp.name)
                    self.assertIn(expected, str(raised.exception))
                    log = local / 'LETransKr' / 'font_tool.log'
                    self.assertIn(str(log), str(raised.exception))
                    self.assertIn(stdout, log.read_text(encoding='utf-8'))
        finally:
            self.run_tool.start()

    def test_tool_output_checked(self):
        self.run_tool.stop()
        try:
            count = len(patcher.KR_FONT_ASSETS)
            plain = ['Making fonts dynamic:'] + ['\tMade dynamic: x'] * count
            bundled = plain + ['Making fonts dynamic in b:'] + ['\tMade dynamic in bundle: x'] * count
            done = '\n'.join(bundled + ['Done!'])
            cases = [(0, done, 1, True), (1, done, 1, False), (0, done.replace('Done!', 'Error'), 1, False),
                     (0, 'Making fonts dynamic:\n\tMade dynamic: x\nDone!', 0, False),
                     (0, '\n'.join(plain + ['Done!']), 0, True), (0, '\n'.join(plain + ['Done!']), 1, False)]
            for code, stdout, bundles, ok in cases:
                completed = patcher.subprocess.CompletedProcess([], code, stdout, '')
                with self.subTest(code=code, stdout=stdout, bundles=bundles), patch.object(patcher, '_write_font_log', return_value=None), \
                        patch.object(patcher.subprocess, 'run', return_value=completed):
                    if ok:
                        self.assertEqual(len(patcher.run_font_tool(self.tool, self.game, self.tmp.name, bundles)), count * (1 + bundles))
                    else:
                        with self.assertRaises(RuntimeError):
                            patcher.run_font_tool(self.tool, self.game, self.tmp.name, bundles)
        finally:
            self.run_tool.start()

    def test_font_tool_asset_detected(self):
        assets = patcher.find_release_assets({'assets': [
            {'name': 'LEFontPatch.exe', 'browser_download_url': 'tool'},
            {'name': 'LastEpoch_KR_Patcher.exe', 'browser_download_url': 'patcher'}]})
        self.assertEqual(assets['font_tool']['url'], 'tool')


class StaleLockTests(unittest.TestCase):
    def test_only_locks_of_dead_processes_are_cleared(self):
        import json, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            font_lock = game / patcher.FONT_BACKUP_DIR_NAME / '.workbench.lock'
            aa_lock = game / Path(patcher.BUNDLE_SUBDIR).parent / '.workbench.lock'
            for lock in (font_lock, aa_lock):
                lock.parent.mkdir(parents=True)
            dead = subprocess.Popen([sys.executable, '-c', 'pass']); dead.wait()
            alive = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            try:
                font_lock.write_text(json.dumps({'pid': dead.pid}), encoding='utf-8')
                aa_lock.write_text(json.dumps({'pid': alive.pid}), encoding='utf-8')
                self.assertEqual(patcher.clear_stale_locks(game), [font_lock])
                self.assertFalse(font_lock.exists()); self.assertTrue(aa_lock.exists())
                # 패처가 도중에 꺼진 뒤 다시 실행: 잠금 때문에 막히지 않아야 함
                with patcher.workspace_lock(font_lock.parent):
                    pass
                with self.assertRaises(RuntimeError), patcher.workspace_lock(aa_lock.parent):
                    pass
            finally:
                alive.kill(); alive.wait()
            font_lock.write_text('', encoding='utf-8')  # 쓰다 만 잠금 파일
            self.assertEqual(set(patcher.clear_stale_locks(game)), {font_lock, aa_lock})


class WorkRootTests(unittest.TestCase):
    def test_staging_avoids_redirected_system_temp(self):
        # TEMP가 보호 폴더(문서)로 바뀐 PC에서 LELocalePatch가 파일을 못 만드는 문제: LOCALAPPDATA에서 작업
        with tempfile.TemporaryDirectory() as tmp:
            game, local, redirected = Path(tmp) / 'aa', Path(tmp) / 'local', Path(tmp) / 'Documents' / 'CreatorTemp'
            bundle = game / 'StandaloneWindows64' / 'ko.bundle'
            bundle.parent.mkdir(parents=True); redirected.mkdir(parents=True)
            bundle.write_bytes(b'original'); (game / 'catalog.bin').write_bytes(b'catalog')
            source = Path(tmp) / 'source'; source.mkdir()
            patcher.write_json(source / 'Skills_ko.json', {'1': '번역'})
            staged = []
            def fake(exe, stage, action, target):
                staged.append(Path(stage))
                if action == 'export':
                    patcher.write_json(Path(target) / 'Skills_ko.json', {'1': '번역'})
            with patch.dict(patcher.os.environ, {'LOCALAPPDATA': str(local)}), \
                    patch.object(patcher.tempfile, 'tempdir', str(redirected)), \
                    patch('locale_runner.run_checked', side_effect=fake):
                patcher.run_lelocale_patch('exe', bundle, 'import', source)
                self.assertTrue(staged and all(local in path.parents for path in staged))
                self.assertEqual(list(redirected.iterdir()), [])
            with patch.dict(patcher.os.environ, {'LOCALAPPDATA': ''}):
                self.assertIsNone(patcher.work_root())


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
