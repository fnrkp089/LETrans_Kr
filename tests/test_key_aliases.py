"""공용 키 번들에 빠진 키 이름을 추가하는 기능. 게임 파일 없이 작은 번들을 만들어 확인."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'patcher') if (ROOT / 'patcher').is_dir() else str(ROOT))
import patcher
import unity_bundle

ALIGN = 0x4000
# (단계, 배열 여부, 타입, 이름, 크기, 플래그): Unity Localization의 SharedTableData와 같은 모양
TREE = [
    (0, 0, "MonoBehaviour", "Base", -1, 0),
    (1, 0, "string", "m_TableCollectionName", -1, 0),
    (2, 1, "Array", "Array", -1, ALIGN),
    (3, 0, "int", "size", 4, 0),
    (3, 0, "char", "data", 1, 0),
    (1, 0, "vector", "m_Entries", -1, 0),
    (2, 1, "Array", "Array", -1, 0),
    (3, 0, "int", "size", 4, 0),
    (3, 0, "SharedTableEntry", "data", -1, 0),
    (4, 0, "SInt64", "m_Id", 8, 0),
    (4, 0, "string", "m_Key", -1, 0),
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
OTHER_TREE = [(0, 0, "MonoBehaviour", "Base", -1, 0), (1, 0, "int", "m_Value", 4, 0)]


def string(text):
    data = text.encode("utf-8")
    return struct.pack("<i", len(data)) + data + bytes(-len(data) % 4)


def table(name, entries):
    """entries: [(id, 키 이름, [rid, ...])]"""
    data = string(name) + struct.pack("<i", len(entries))
    for key_id, key, items in entries:
        data += struct.pack("<q", key_id) + string(key) + struct.pack("<i", len(items)) + b"".join(struct.pack("<q", i) for i in items)
    return data + struct.pack("<i", 0x7A11)


def type_entry(tree):
    strings, offsets = b"", {}
    for _, _, type_name, name, _, _ in tree:
        for text in (type_name, name):
            if text not in offsets:
                offsets[text] = len(strings)
                strings += text.encode() + b"\0"
    nodes = b"".join(struct.pack("<HBBIIiiIQ", 1, level, array, offsets[type_name], offsets[name], size, index, meta, 0)
                     for index, (level, array, type_name, name, size, meta) in enumerate(tree))
    return struct.pack("<iBh", 114, 0, 0) + bytes(32) + struct.pack("<ii", len(tree), len(strings)) + nodes + strings + struct.pack("<i", 0)


def serialized_file(objects):
    """objects: [(타입 번호, 바이트)]"""
    meta = b"6000.4.8f1\0" + struct.pack("<iB", 19, 1) + struct.pack("<i", 2) + type_entry(TREE) + type_entry(OTHER_TREE)
    meta += struct.pack("<i", len(objects))
    body = b""
    for path_id, (type_index, data) in enumerate(objects, 1):
        meta += bytes(-(48 + len(meta)) % 4)
        body += bytes(-len(body) % 16)
        meta += struct.pack("<qqIi", path_id, len(body), len(data), type_index)
        body += data
    meta += struct.pack("<iii", 0, 0, 0) + b"\0"
    data_offset = (48 + len(meta) + 15) & ~15
    head = struct.pack(">IIII", 0, 0, 22, 0) + bytes(4) + struct.pack(">Iqqq", len(meta), data_offset + len(body), data_offset, 0)
    return head + meta + bytes(data_offset - 48 - len(meta)) + body


def bundle(content):
    info = bytes(16) + struct.pack(">i", 1) + struct.pack(">IIH", len(content), len(content), 0)
    info += struct.pack(">i", 1) + struct.pack(">qqI", 0, len(content), 4) + b"CAB-test\0"
    head = b"UnityFS\0" + struct.pack(">I", 8) + b"5.x.x\0" + b"6000.4.8f1\0"
    size_at = len(head)
    head += struct.pack(">qIII", 0, len(info), len(info), 0x40)
    head += bytes(-len(head) % 16) + info
    head = head[:size_at] + struct.pack(">q", len(head) + len(content)) + head[size_at + 8:]
    return head + content


AFFIXES = [(11, "Item_Affix_1155_DisplayName", []), (22, "Item_Affix_1156_LootFilterOverride", [7, 8]), (33, "Item_Affix_1157_DisplayName", [])]
ALIASES = {"Item_Affix_1156_DisplayName": "Item_Affix_1156_LootFilterOverride"}


def sample():
    return bundle(serialized_file([(0, table("UI", [(1, "Start", [])])), (1, struct.pack("<i", 5)), (0, table("Item_Affixes", AFFIXES))]))


class BundleTests(unittest.TestCase):
    def test_alias_added_with_same_id_and_nothing_else_changes(self):
        raw = sample()
        before = unity_bundle.list_keys(raw)
        patched, added = unity_bundle.add_key_aliases(raw, ALIASES)
        self.assertEqual(added, ["Item_Affix_1156_DisplayName"])
        self.assertEqual(unity_bundle.list_keys(patched), dict(before, Item_Affix_1156_DisplayName=22))
        old, new = (unity_bundle.SerializedFile(unity_bundle.Bundle(b).file()) for b in (raw, patched))
        self.assertEqual([new.object_bytes(i) for i in (0, 1)], [old.object_bytes(i) for i in (0, 1)])
        # 새 항목은 원래 항목의 메타데이터까지 그대로 가진 채 목록 끝에 붙고, 뒤따르는 필드는 그대로
        expected = table("Item_Affixes", AFFIXES + [(22, "Item_Affix_1156_DisplayName", [7, 8])])
        self.assertEqual(new.object_bytes(2), expected)

    def test_already_present_or_unknown_source_is_left_alone(self):
        patched, _ = unity_bundle.add_key_aliases(sample(), ALIASES)
        self.assertEqual(unity_bundle.add_key_aliases(patched, ALIASES), (None, []))
        self.assertEqual(unity_bundle.add_key_aliases(sample(), {"New_Key": "No_Such_Key"}), (None, []))

    def test_rewritten_bundle_keeps_untouched_content(self):
        raw = sample()
        loaded = unity_bundle.Bundle(raw)
        again = unity_bundle.Bundle(loaded.to_bytes())
        self.assertEqual((again.data, again.nodes), (loaded.data, loaded.nodes))
        self.assertEqual(unity_bundle.SerializedFile(loaded.file()).to_bytes(), loaded.file())

    def test_lz4_literals_and_overlapping_match(self):
        packed = bytes([0x22]) + b"ab" + bytes([2, 0]) + bytes([0x10]) + b"c"
        self.assertEqual(unity_bundle.lz4_decompress(packed, 9), b"ababababc")
        with self.assertRaises(unity_bundle.BundleError):
            unity_bundle.lz4_decompress(packed, 8)

    def test_not_a_bundle_rejected(self):
        with self.assertRaises(unity_bundle.BundleError):
            unity_bundle.add_key_aliases(b"not a bundle at all", ALIASES)


class PatcherKeyAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.game = Path(self.tmp.name)
        self.bundle = self.game / patcher.BUNDLE_SUBDIR / patcher.BUNDLE_FILENAME
        self.bundle.parent.mkdir(parents=True)
        self.catalog = self.game / patcher.CATALOG_RELPATH
        self.shared = self.bundle.with_name(patcher.SHARED_BUNDLE_FILENAME)
        self.bundle.write_bytes(b'original bundle')
        self.catalog.write_bytes(b'original catalog')
        self.shared.write_bytes(sample())
        self.build = patch.object(patcher, 'get_steam_buildid', return_value='100')
        self.build.start()
        self.state = patcher.PatchState(self.game)

    def tearDown(self):
        self.build.stop()
        self.tmp.cleanup()

    def test_original_is_backed_up_and_restored_with_the_catalog(self):
        original = self.shared.read_bytes()
        patcher.create_backup(self.game)
        self.assertEqual(patcher.apply_key_aliases(self.game, self.state), list(ALIASES))
        self.assertIn("Item_Affix_1156_DisplayName", unity_bundle.list_keys(self.shared.read_bytes()))
        self.assertEqual(self.state.data["shared_bundle_hash"], patcher.sha256_file(self.shared))
        # 다시 적용해도 백업은 원본 그대로
        self.assertEqual(patcher.apply_key_aliases(self.game, self.state), [])
        patcher.create_backup(self.game)
        self.assertEqual((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).read_bytes(), original)
        self.catalog.write_bytes(b'patched catalog')
        self.assertTrue(patcher.restore_backup(self.game))
        self.assertEqual(self.shared.read_bytes(), original)
        self.assertEqual(self.catalog.read_bytes(), b'original catalog')

    def test_without_backup_the_game_file_is_not_touched(self):
        original = self.shared.read_bytes()
        with self.assertRaises(RuntimeError):
            patcher.apply_key_aliases(self.game, self.state)
        self.assertEqual(self.shared.read_bytes(), original)

    def test_game_without_shared_bundle_or_with_fixed_keys_is_skipped(self):
        patcher.create_backup(self.game)
        fixed, _ = unity_bundle.add_key_aliases(self.shared.read_bytes(), ALIASES)
        self.shared.write_bytes(fixed)
        self.assertEqual(patcher.apply_key_aliases(self.game, self.state), [])
        self.assertFalse((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).exists())
        self.shared.unlink()
        self.assertEqual(patcher.apply_key_aliases(self.game, self.state), [])

    def test_game_update_keeping_patched_bundle_carries_old_original(self):
        original = self.shared.read_bytes()
        patcher.create_backup(self.game)
        patcher.apply_key_aliases(self.game, self.state)
        self.bundle.write_bytes(b'new game bundle')
        self.catalog.write_bytes(b'new game catalog')
        with patch.object(patcher, 'get_steam_buildid', return_value='101'):
            patcher.create_backup(self.game)
            self.assertEqual((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).read_bytes(), original)
            self.catalog.write_bytes(b'patched catalog')
            self.assertTrue(patcher.restore_backup(self.game))
        self.assertEqual(self.shared.read_bytes(), original)

    def test_game_update_replacing_bundle_backs_up_the_new_one(self):
        patcher.create_backup(self.game)
        patcher.apply_key_aliases(self.game, self.state)
        replaced = bundle(serialized_file([(0, table("Item_Affixes", AFFIXES + [(44, "New_Key", [])]))]))
        self.shared.write_bytes(replaced)
        with patch.object(patcher, 'get_steam_buildid', return_value='101'):
            patcher.create_backup(self.game)
            self.assertFalse((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).exists())
            self.assertEqual(patcher.apply_key_aliases(self.game, self.state), list(ALIASES))
            self.assertEqual((self.game / patcher.BACKUP_DIR_NAME / self.shared.name).read_bytes(), replaced)


if __name__ == "__main__":
    unittest.main()
