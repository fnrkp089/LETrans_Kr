"""게임에 키가 없어 영어로만 나오는 글에 키와 한국어 글을 추가하는 기능. 게임 파일 없이 작은 번들로 확인."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'patcher') if (ROOT / 'patcher').is_dir() else str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import patcher
import unity_bundle
from test_key_aliases import ALIGN, TREE, bundle, string, table, type_entry

# Unity Localization의 StringTable과 같은 모양
STRING_TREE = [
    (0, 0, "MonoBehaviour", "Base", -1, 0),
    (1, 0, "PPtr<GameObject>", "m_GameObject", 12, 0),
    (1, 0, "UInt8", "m_Enabled", 1, ALIGN),
    (1, 0, "PPtr<MonoScript>", "m_Script", 12, 0),
    (1, 0, "string", "m_Name", -1, 0),
    (2, 1, "Array", "Array", -1, ALIGN),
    (3, 0, "int", "size", 4, 0),
    (3, 0, "char", "data", 1, 0),
    (1, 0, "vector", "m_TableData", -1, 0),
    (2, 1, "Array", "Array", -1, 0),
    (3, 0, "int", "size", 4, 0),
    (3, 0, "TableEntryData", "data", -1, 0),
    (4, 0, "SInt64", "m_Id", 8, 0),
    (4, 0, "string", "m_Localized", -1, 0),
    (5, 1, "Array", "Array", -1, ALIGN),
    (6, 0, "int", "size", 4, 0),
    (6, 0, "char", "data", 1, 0),
    (4, 0, "MetadataCollection", "m_Metadata", -1, 0),
    (5, 0, "vector", "m_Items", -1, 0),
    (6, 1, "Array", "Array", -1, 0),
    (7, 0, "int", "size", 4, 0),
    (7, 0, "SInt64", "rid", 8, 0),
    (1, 0, "int", "m_Tail", 4, 0),
]


def string_table(name, entries):
    """entries: [(id, 글, [rid, ...])]"""
    data = bytes(12) + struct.pack("<i", 1) + bytes(12) + string(name) + struct.pack("<i", len(entries))
    for entry_id, text, items in entries:
        data += struct.pack("<q", entry_id) + string(text) + struct.pack("<i", len(items)) + b"".join(struct.pack("<q", i) for i in items)
    return data + struct.pack("<i", 0x7A11)


def serialized_file(tree, objects):
    meta = b"6000.4.8f1\0" + struct.pack("<iB", 19, 1) + struct.pack("<i", 1) + type_entry(tree)
    meta += struct.pack("<i", len(objects))
    body = b""
    for path_id, data in enumerate(objects, 1):
        meta += bytes(-(48 + len(meta)) % 4)
        body += bytes(-len(body) % 16)
        meta += struct.pack("<qqIi", path_id, len(body), len(data), 0)
        body += data
    meta += struct.pack("<iii", 0, 0, 0) + b"\0"
    data_offset = (48 + len(meta) + 15) & ~15
    head = struct.pack(">IIII", 0, 0, 22, 0) + bytes(4) + struct.pack(">Iqqq", len(meta), data_offset + len(body), data_offset, 0)
    return head + meta + bytes(data_offset - 48 - len(meta)) + body


ABILITY_KEYS = [(101, "Ability_Fireball_Name", []), (102, "Ability_Fireball_Description", [5])]
ABILITY_TEXTS = [(101, "화염구", []), (102, "불덩이를 던집니다.", [5])]
OTHER_TEXTS = [(101, "다른 표의 같은 번호", [])]
NEW = {"Ability_Rage Aura_Description": 9001, "Ability_Rage Aura_AltText": 9002}


def shared_sample(extra=()):
    return bundle(serialized_file(TREE, [table("UI", [(1, "Start", [])]), table("Abilities", ABILITY_KEYS + list(extra))]))


def strings_sample():
    return bundle(serialized_file(STRING_TREE, [string_table("UI_ko", OTHER_TEXTS), string_table("Abilities_ko", ABILITY_TEXTS)]))


def objects(raw):
    assets = unity_bundle.SerializedFile(unity_bundle.Bundle(raw).file())
    return [assets.object_bytes(i) for i in range(len(assets.objects))]


class BundleTests(unittest.TestCase):
    def test_keys_added_only_to_the_named_table(self):
        raw = shared_sample()
        patched, resolved = unity_bundle.add_keys(raw, "Abilities", NEW)
        self.assertEqual(resolved, NEW)
        self.assertEqual(unity_bundle.list_keys(patched), dict(unity_bundle.list_keys(raw), **NEW))
        expected = table("Abilities", ABILITY_KEYS + [(9001, "Ability_Rage Aura_Description", []), (9002, "Ability_Rage Aura_AltText", [])])
        self.assertEqual(objects(patched), [objects(raw)[0], expected])

    def test_existing_name_keeps_its_id_and_taken_id_is_skipped(self):
        raw = shared_sample()
        wanted = {"Ability_Fireball_Name": 9001, "Ability_New_Description": 102, "Ability_Other_Description": 9003}
        patched, resolved = unity_bundle.add_keys(raw, "Abilities", wanted)
        # 있는 이름은 게임의 id 그대로, 다른 이름이 쓰는 id(102)는 추가하지 않음
        self.assertEqual(resolved, {"Ability_Fireball_Name": 101, "Ability_Other_Description": 9003})
        self.assertEqual(unity_bundle.list_keys(patched), dict(unity_bundle.list_keys(raw), Ability_Other_Description=9003))
        self.assertEqual(unity_bundle.add_keys(patched, "Abilities", wanted), (None, resolved))
        self.assertEqual(unity_bundle.add_keys(raw, "No_Such_Table", NEW), (None, {}))

    def test_strings_added_and_replaced_in_the_named_table(self):
        raw = strings_sample()
        texts = {102: "불덩이를 날립니다.", 9001: "격노의 오라를 내뿜습니다."}
        patched, changed = unity_bundle.set_strings(raw, "Abilities_ko", texts)
        self.assertEqual(changed, [102, 9001])
        self.assertEqual(unity_bundle.list_strings(patched, "Abilities_ko"), {101: "화염구", **texts})
        # 바꾼 항목은 메타데이터를 그대로 갖고, 다른 표와 뒤따르는 필드는 그대로
        expected = string_table("Abilities_ko", [(101, "화염구", []), (102, texts[102], [5]), (9001, texts[9001], [])])
        self.assertEqual(objects(patched), [objects(raw)[0], expected])
        self.assertEqual(unity_bundle.set_strings(patched, "Abilities_ko", texts), (None, []))
        self.assertEqual(unity_bundle.set_strings(raw, "No_Such_Table_ko", texts), (None, []))


class PatcherAddedKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name)
        self.bundle = self.game / patcher.BUNDLE_SUBDIR / patcher.BUNDLE_FILENAME
        self.bundle.parent.mkdir(parents=True)
        self.catalog = self.game / patcher.CATALOG_RELPATH
        self.shared = self.bundle.with_name(patcher.SHARED_BUNDLE_FILENAME)
        self.bundle.write_bytes(strings_sample())
        self.catalog.write_bytes(b'original catalog')
        self.shared.write_bytes(shared_sample())
        self.build = patch.object(patcher, 'get_steam_buildid', return_value='100')
        self.build.start()
        self.state = patcher.PatchState(self.game)
        self.tables = {"Abilities": {"Ability_Rage Aura_Description": (9001, "격노의 오라를 내뿜습니다."),
                                     "Ability_Rage Aura_AltText": (9002, "추가 주문 피해가 매초 300%의 효율로 적용됩니다.")}}

    def tearDown(self):
        self.build.stop()
        self.tmp.cleanup()

    def texts(self):
        return unity_bundle.list_strings(self.bundle.read_bytes(), "Abilities_ko")

    def test_keys_and_texts_are_added_and_restore_brings_originals_back(self):
        originals = self.shared.read_bytes(), self.bundle.read_bytes()
        patcher.create_backup(self.game)
        self.assertEqual(patcher.apply_added_keys(self.game, self.state, self.tables), list(self.tables["Abilities"]))
        keys = unity_bundle.list_keys(self.shared.read_bytes())
        self.assertEqual((keys["Ability_Rage Aura_Description"], keys["Ability_Rage Aura_AltText"]), (9001, 9002))
        self.assertEqual(self.texts()[9001], "격노의 오라를 내뿜습니다.")
        self.assertEqual(self.texts()[101], "화염구")
        self.assertEqual(self.state.data["shared_bundle_hash"], patcher.sha256_file(self.shared))
        # 다시 적용하면 파일은 그대로, 결과는 같음
        after = self.shared.read_bytes(), self.bundle.read_bytes()
        self.assertEqual(patcher.apply_added_keys(self.game, self.state, self.tables), list(self.tables["Abilities"]))
        self.assertEqual((self.shared.read_bytes(), self.bundle.read_bytes()), after)
        self.catalog.write_bytes(b'patched catalog')
        self.assertTrue(patcher.restore_backup(self.game))
        self.assertEqual((self.shared.read_bytes(), self.bundle.read_bytes()), originals)

    def test_changed_translation_replaces_our_text(self):
        patcher.create_backup(self.game)
        patcher.apply_added_keys(self.game, self.state, self.tables)
        self.tables["Abilities"]["Ability_Rage Aura_Description"] = (9001, "고친 번역")
        patcher.apply_added_keys(self.game, self.state, self.tables)
        self.assertEqual(self.texts()[9001], "고친 번역")

    def test_alias_and_added_keys_together_keep_the_original_backup(self):
        original = self.shared.read_bytes()
        patcher.create_backup(self.game)
        with patch.object(patcher, 'KEY_ALIASES', {"Ability_Fireball_Title": "Ability_Fireball_Name"}):
            self.assertEqual(patcher.apply_key_aliases(self.game, self.state), ["Ability_Fireball_Title"])
        patcher.apply_added_keys(self.game, self.state, self.tables)
        self.assertEqual((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).read_bytes(), original)
        self.assertIn("Ability_Fireball_Title", unity_bundle.list_keys(self.shared.read_bytes()))

    def test_key_the_game_already_has_is_left_to_the_game(self):
        self.shared.write_bytes(shared_sample([(555, "Ability_Rage Aura_Description", [])]))
        patcher.create_backup(self.game)
        self.assertEqual(patcher.apply_added_keys(self.game, self.state, self.tables), ["Ability_Rage Aura_AltText"])
        self.assertEqual(unity_bundle.list_keys(self.shared.read_bytes())["Ability_Rage Aura_Description"], 555)
        self.assertNotIn(9001, self.texts())
        self.assertNotIn(555, self.texts())

    def test_without_backup_the_game_files_are_not_touched(self):
        originals = self.shared.read_bytes(), self.bundle.read_bytes()
        with self.assertRaises(RuntimeError):
            patcher.apply_added_keys(self.game, self.state, self.tables)
        self.assertEqual((self.shared.read_bytes(), self.bundle.read_bytes()), originals)
        self.assertEqual(patcher.apply_added_keys(self.game, self.state, {}), [])

    def test_package_file_is_checked(self):
        path = self.game / patcher.ADDED_KEYS_FILE
        path.write_text(json.dumps({"Abilities": {"Key": {"id": 9001, "en": "Text", "ko": "글"}}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(patcher.load_added_keys(path), {"Abilities": {"Key": (9001, "글")}})
        for bad in ({"Abilities": {"Key": {"id": "9001", "ko": "글"}}}, {"Abilities": {"Key": {"id": 9001, "ko": " "}}},
                    {"Abilities": {"Key": "글"}}, {"Abilities": []}, []):
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                patcher.load_added_keys(path)

    def test_shipped_file_loads_with_unique_ids(self):
        shipped = Path(patcher.__file__).resolve().parent / patcher.ADDED_KEYS_FILE
        if not shipped.is_file():
            self.skipTest("added_keys.json 없음")
        tables = patcher.load_added_keys(shipped)
        for keys in tables.values():
            ids = [key_id for key_id, _ in keys.values()]
            self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
