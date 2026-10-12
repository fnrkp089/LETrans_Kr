"""
Unity 에셋 번들(UnityFS)의 현지화 공용 키 테이블(SharedTableData)에 키 이름을 추가

게임이 찾는 키 이름이 공용 키 번들에 빠져 있으면 번역이 있어도 화면에 키 번호가 그대로 나옴.
LELocalePatch는 있는 키의 글만 바꿀 수 있어서, 빠진 키 이름은 여기서 기존 항목의 별칭으로 추가함.
표준 라이브러리만 사용 (패처 런타임에 외부 패키지 없음).
"""

import lzma
import struct

SIGNATURE = b"UnityFS\0"
COMPRESSION_MASK = 0x3F
BLOCKS_INFO_AT_END = 0x80
BLOCK_INFO_PADDING = 0x200
BLOCK_SIZE = 0x20000
MONO_BEHAVIOUR = 114
ALIGN_FLAG = 0x4000


class BundleError(Exception):
    pass


def _align(value, size):
    return (value + size - 1) & ~(size - 1)


def _cstring(data, pos):
    end = data.index(b"\0", pos)
    return data[pos:end].decode("utf-8"), end + 1


def lz4_decompress(source, size):
    out = bytearray()
    pos, end = 0, len(source)
    while pos < end:
        token = source[pos]
        pos += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = source[pos]
                pos += 1
                length += extra
                if extra != 255:
                    break
        out += source[pos:pos + length]
        pos += length
        if pos >= end:
            break
        offset = source[pos] | (source[pos + 1] << 8)
        pos += 2
        length = token & 15
        if length == 15:
            while True:
                extra = source[pos]
                pos += 1
                length += extra
                if extra != 255:
                    break
        length += 4
        start = len(out) - offset
        if offset == 0 or start < 0:
            raise BundleError("LZ4 데이터 손상")
        if offset >= length:
            out += out[start:start + length]
        else:  # 겹치는 복사: 앞에서 쓴 바이트를 반복
            chunk = bytes(out[start:])
            out += (chunk * (length // offset + 1))[:length]
    if len(out) != size:
        raise BundleError("LZ4 압축 해제 크기 불일치")
    return bytes(out)


def _decompress(chunk, size, method):
    if method == 0:
        data = bytes(chunk)
    elif method == 1:
        filters = [lzma._decode_filter_properties(lzma.FILTER_LZMA1, bytes(chunk[:5]))]
        data = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(bytes(chunk[5:]), size)
    elif method in (2, 3):
        data = lz4_decompress(chunk, size)
    else:
        raise BundleError(f"지원하지 않는 압축 방식: {method}")
    if len(data) != size:
        raise BundleError("압축 해제 크기 불일치")
    return data


class Bundle:
    """UnityFS 컨테이너. data는 블록을 모두 푼 내용, nodes는 그 안의 파일 목록 [오프셋, 크기, 플래그, 이름]."""

    def __init__(self, raw):
        raw = memoryview(raw)
        if bytes(raw[:8]) != SIGNATURE:
            raise BundleError("UnityFS 번들이 아님")
        pos = 8
        (self.version,) = struct.unpack_from(">I", raw, pos)
        pos += 4
        head = bytes(raw[:256])
        self.player_version, pos = _cstring(head, pos)
        self.engine_version, pos = _cstring(head, pos)
        size, info_compressed, info_size, self.flags = struct.unpack_from(">qIII", raw, pos)
        pos += 20
        if size != len(raw):
            raise BundleError("번들 크기가 헤더와 다름")
        if self.version >= 7:
            pos = _align(pos, 16)
        if self.flags & BLOCKS_INFO_AT_END:
            info = raw[len(raw) - info_compressed:]
        else:
            info = raw[pos:pos + info_compressed]
            pos += info_compressed
        info = _decompress(info, info_size, self.flags & COMPRESSION_MASK)
        self.hash = info[:16]
        (count,) = struct.unpack_from(">i", info, 16)
        at = 20
        blocks = []
        for _ in range(count):
            blocks.append(struct.unpack_from(">IIH", info, at))
            at += 10
        (count,) = struct.unpack_from(">i", info, at)
        at += 4
        self.nodes = []
        for _ in range(count):
            offset, length, flags = struct.unpack_from(">qqI", info, at)
            name, at = _cstring(info, at + 20)
            self.nodes.append([offset, length, flags, name])
        if self.flags & BLOCK_INFO_PADDING:
            pos = _align(pos, 16)
        data = bytearray()
        for size, compressed, flags in blocks:
            data += _decompress(raw[pos:pos + compressed], size, flags & COMPRESSION_MASK)
            pos += compressed
        self.data = bytes(data)

    def file(self, index=0):
        offset, length, _, _ = self.nodes[index]
        return self.data[offset:offset + length]

    def replace_file(self, index, content):
        offset, length, _, _ = self.nodes[index]
        self.data = self.data[:offset] + content + self.data[offset + length:]
        shift = len(content) - length
        self.nodes[index][1] = len(content)
        for node in self.nodes:
            if node[0] > offset:
                node[0] += shift

    def to_bytes(self):
        """압축하지 않은 블록으로 다시 기록 (Unity는 압축 여부와 무관하게 읽음)."""
        info = bytearray(self.hash)
        sizes = [min(BLOCK_SIZE, len(self.data) - at) for at in range(0, len(self.data), BLOCK_SIZE)]
        info += struct.pack(">i", len(sizes))
        for size in sizes:
            info += struct.pack(">IIH", size, size, 0)
        info += struct.pack(">i", len(self.nodes))
        for offset, length, flags, name in self.nodes:
            info += struct.pack(">qqI", offset, length, flags) + name.encode("utf-8") + b"\0"
        flags = self.flags & ~COMPRESSION_MASK & ~BLOCKS_INFO_AT_END
        head = bytearray(SIGNATURE + struct.pack(">I", self.version))
        head += self.player_version.encode("utf-8") + b"\0" + self.engine_version.encode("utf-8") + b"\0"
        size_at = len(head)
        head += struct.pack(">qIII", 0, len(info), len(info), flags)
        if self.version >= 7:
            head += bytes(_align(len(head), 16) - len(head))
        head += info
        if flags & BLOCK_INFO_PADDING:
            head += bytes(_align(len(head), 16) - len(head))
        struct.pack_into(">q", head, size_at, len(head) + len(self.data))
        return bytes(head) + self.data


class _Node:
    __slots__ = ("level", "is_array", "type", "name", "size", "meta", "children")


class SerializedFile:
    """번들 안의 에셋 파일. 타입 트리가 들어 있는 최신 형식(버전 22 이상)만 다룸."""

    def __init__(self, content):
        self.content = content
        (self.version,) = struct.unpack_from(">I", content, 8)
        if self.version < 22:
            raise BundleError(f"지원하지 않는 에셋 파일 버전: {self.version}")
        if content[16] != 0:
            raise BundleError("빅 엔디언 에셋 파일은 지원하지 않음")
        metadata_size, file_size, self.data_offset = struct.unpack_from(">Iqq", content, 20)
        if file_size != len(content):
            raise BundleError("에셋 파일 크기가 헤더와 다름")
        _, pos = _cstring(content, 48)
        pos += 4  # 플랫폼
        if not content[pos]:
            raise BundleError("타입 트리가 없는 에셋 파일")
        (count,) = struct.unpack_from("<i", content, pos + 1)
        pos += 5
        self.types = []
        for _ in range(count):
            (class_id,) = struct.unpack_from("<i", content, pos)
            (script_index,) = struct.unpack_from("<h", content, pos + 5)
            pos += 7 + 16 + (16 if class_id == MONO_BEHAVIOUR or script_index >= 0 else 0)
            node_count, string_size = struct.unpack_from("<ii", content, pos)
            pos += 8
            strings = content[pos + node_count * 32:pos + node_count * 32 + string_size]
            nodes = []
            for i in range(node_count):
                _, level, flags, type_at, name_at, size, _, meta = struct.unpack_from("<HBBIIiiI", content, pos + i * 32)
                node = _Node()
                node.level, node.is_array, node.size, node.meta, node.children = level, bool(flags & 1), size, meta, []
                # 최상위 비트가 켜진 이름은 Unity 공통 문자열표 참조라 여기서는 읽지 않음
                node.type = "" if type_at & 0x80000000 else _cstring(strings, type_at)[0]
                node.name = "" if name_at & 0x80000000 else _cstring(strings, name_at)[0]
                nodes.append(node)
            stack = []
            for node in nodes:
                del stack[node.level:]
                if stack:
                    stack[-1].children.append(node)
                stack.append(node)
            pos += node_count * 32 + string_size
            (dependencies,) = struct.unpack_from("<i", content, pos)
            pos += 4 + dependencies * 4
            self.types.append((class_id, nodes[0] if nodes else None))
        (count,) = struct.unpack_from("<i", content, pos)
        pos += 4
        self.objects = []  # [표 위치, 시작, 크기, 타입 번호]
        for _ in range(count):
            pos = _align(pos, 4)
            _, start, size, type_index = struct.unpack_from("<qqIi", content, pos)
            self.objects.append([pos, start, size, type_index])
            pos += 24
        self.replaced = {}

    def object_bytes(self, index):
        _, start, size, _ = self.objects[index]
        return self.content[self.data_offset + start:self.data_offset + start + size]

    def to_bytes(self):
        """바뀐 오브젝트를 넣고 데이터 영역을 다시 배치."""
        head = bytearray(self.content[:self.data_offset])
        body = bytearray()
        for index in sorted(range(len(self.objects)), key=lambda i: self.objects[i][1]):
            entry, _, _, _ = self.objects[index]
            data = self.replaced.get(index, self.object_bytes(index))
            body += bytes(_align(len(body), 16) - len(body))
            struct.pack_into("<qI", head, entry + 8, len(body), len(data))
            body += data
        struct.pack_into(">q", head, 24, len(head) + len(body))
        return bytes(head) + bytes(body)


def _skip(node, data, pos):
    """타입 트리대로 값 하나를 건너뛴 위치."""
    if node.is_array:
        (count,) = struct.unpack_from("<i", data, pos)
        pos += 4
        element = node.children[1]
        if count < 0:
            raise BundleError("배열 크기 손상")
        if not element.children and not element.is_array:
            pos += count * element.size
        else:
            for _ in range(count):
                pos = _skip(element, data, pos)
    elif node.children:
        for child in node.children:
            pos = _skip(child, data, pos)
    else:
        if node.size < 0:
            raise BundleError("크기를 알 수 없는 필드")
        pos += node.size
    if node.meta & ALIGN_FLAG:
        pos = _align(pos, 4)
    if pos > len(data):
        raise BundleError("오브젝트 범위를 벗어남")
    return pos


def _child(node, name):
    return next((c for c in node.children if c.name == name), None)


def _key_table(root):
    """SharedTableData 모양이면 (m_Entries 배열 노드, 항목 노드, m_Key 노드). 아니면 None."""
    entries = _child(root, "m_Entries") if root else None
    if not entries or len(entries.children) != 1 or not entries.children[0].is_array:
        return None
    array = entries.children[0]
    element = array.children[1]
    key = _child(element, "m_Key")
    if not key or not element.children or element.children[0].name != "m_Id" or element.children[0].size != 8:
        return None
    if element.children.index(key) != 1 or len(key.children) != 1 or not key.children[0].is_array:
        return None
    return entries, element, key


def _add_aliases(root, data, aliases):
    """오브젝트 하나의 키 목록에 별칭을 추가한 새 바이트와 추가한 이름들. 해당 없으면 (None, [])."""
    table = _key_table(root)
    if not table:
        return None, []
    entries, element, key = table
    pos = 0
    for child in root.children:
        if child is entries:
            break
        pos = _skip(child, data, pos)
    (count,) = struct.unpack_from("<i", data, pos)
    found = {}
    at = pos + 4
    for _ in range(count):
        (length,) = struct.unpack_from("<i", data, at + 8)
        name = bytes(data[at + 12:at + 12 + length])
        after_key = _skip(key, data, at + 8)
        end = _skip(element, data, at)
        found[name] = (bytes(data[at:at + 8]), bytes(data[after_key:end]))
        at = end
    added, extra = [], bytearray()
    for alias, source in aliases.items():
        alias_bytes, source_bytes = alias.encode("utf-8"), source.encode("utf-8")
        if alias_bytes in found or source_bytes not in found:
            continue
        key_id, rest = found[source_bytes]
        extra += key_id + struct.pack("<i", len(alias_bytes)) + alias_bytes
        extra += bytes(_align(len(alias_bytes), 4) - len(alias_bytes)) + rest
        added.append(alias)
    if not added:
        return None, []
    # 항목은 4바이트 정렬로 끝나므로 마지막 항목 뒤(at)에 그대로 이어 붙임
    return bytes(data[:pos]) + struct.pack("<i", count + len(added)) + bytes(data[pos + 4:at]) + bytes(extra) + bytes(data[at:]), added


def add_key_aliases(raw, aliases):
    """번들 바이트에 {새 키 이름: 같은 글을 가리킬 기존 키 이름}을 추가. (새 번들 바이트, 추가한 이름들) 반환.

    새 이름이 이미 있거나 기존 키가 없으면 그 항목은 건너뜀. 추가한 것이 없으면 (None, []).
    """
    bundle = Bundle(raw)
    added = []
    for index, (_, _, _, name) in enumerate(bundle.nodes):
        if name.endswith((".resS", ".resource")):
            continue
        assets = SerializedFile(bundle.file(index))
        for number, (_, _, _, type_index) in enumerate(assets.objects):
            class_id, root = assets.types[type_index]
            if class_id != MONO_BEHAVIOUR:
                continue
            data, names = _add_aliases(root, assets.object_bytes(number), aliases)
            if names:
                assets.replaced[number] = data
                added += names
        if assets.replaced:
            bundle.replace_file(index, assets.to_bytes())
    if not added:
        return None, []
    patched = bundle.to_bytes()
    # 다시 읽어서 구조가 온전하고 기존 키가 그대로인지 확인한 것만 돌려줌
    before, after = list_keys(raw), list_keys(patched)
    if after != dict(before, **{alias: before[aliases[alias]] for alias in added}):
        raise BundleError("수정한 번들 확인 실패")
    return patched, added


def _rewrite(raw, change):
    """번들의 모든 MonoBehaviour에 change(타입 트리, 바이트)를 적용. 새 바이트를 돌려준 오브젝트만 바꿈.

    바뀐 것이 없으면 None.
    """
    bundle = Bundle(raw)
    touched = False
    for index, (_, _, _, name) in enumerate(bundle.nodes):
        if name.endswith((".resS", ".resource")):
            continue
        assets = SerializedFile(bundle.file(index))
        for number, (_, _, _, type_index) in enumerate(assets.objects):
            class_id, root = assets.types[type_index]
            if class_id != MONO_BEHAVIOUR or not root:
                continue
            data = change(root, assets.object_bytes(number))
            if data is not None:
                assets.replaced[number] = data
        if assets.replaced:
            bundle.replace_file(index, assets.to_bytes())
            touched = True
    return bundle.to_bytes() if touched else None


def _offset(root, data, target):
    """최상위 필드 target이 시작하는 위치."""
    pos = 0
    for child in root.children:
        if child is target:
            return pos
        pos = _skip(child, data, pos)
    raise BundleError("필드를 찾을 수 없음")


def _text(data, pos):
    (length,) = struct.unpack_from("<i", data, pos)
    return bytes(data[pos + 4:pos + 4 + length])


def _packed(text):
    return struct.pack("<i", len(text)) + text + bytes(_align(len(text), 4) - len(text))


def _is_text(node):
    return len(node.children) == 1 and node.children[0].is_array and node.children[0].children[1].size == 1


def _string_table(root):
    """StringTable 모양이면 (m_Name 노드, m_TableData 배열 노드, 항목 노드). 아니면 None."""
    data = _child(root, "m_TableData") if root else None
    if not data or len(data.children) != 1 or not data.children[0].is_array or len(root.children) < 4:
        return None
    element = data.children[0].children[1]
    names = [c.name for c in element.children]
    if names[:2] != ["m_Id", "m_Localized"] or element.children[0].size != 8 or not _is_text(element.children[1]):
        return None
    # MonoBehaviour의 네 번째 필드가 m_Name (이름은 Unity 공통 문자열표 참조라 읽히지 않음)
    if not _is_text(root.children[3]):
        return None
    return root.children[3], data, element


def add_keys(raw, table, keys):
    """공용 키 번들의 table 표에 {키 이름: id}를 새 항목으로 추가. (새 번들 바이트, {키 이름: 표에서 쓰는 id}) 반환.

    이름이 이미 있으면 건드리지 않고 있는 id를 알려 줌. id를 다른 이름이 쓰고 있으면 그 이름은 결과에서 빠짐.
    추가한 것이 없으면 새 번들 바이트는 None.
    """
    resolved, added = {}, {}

    def change(root, data):
        found, name = _key_table(root), _child(root, "m_TableCollectionName")
        if not found or not name or not _is_text(name) or _text(data, _offset(root, data, name)) != table.encode("utf-8"):
            return None
        entries, element, _ = found
        pos = _offset(root, data, entries)
        (count,) = struct.unpack_from("<i", data, pos)
        existing, at = {}, pos + 4
        for _ in range(count):
            (key_id,) = struct.unpack_from("<q", data, at)
            existing[_text(data, at + 8)] = key_id
            at = _skip(element, data, at)
        used, extra, new = set(existing.values()), bytearray(), 0
        for key, key_id in keys.items():
            key_bytes = key.encode("utf-8")
            if key_bytes in existing:
                resolved[key] = existing[key_bytes]
            elif key_id not in used:
                # 메타데이터 없는 항목: id, 이름, 빈 목록
                extra += struct.pack("<q", key_id) + _packed(key_bytes) + struct.pack("<i", 0)
                used.add(key_id)
                resolved[key] = added[key] = key_id
                new += 1
        if not new:
            return None
        return bytes(data[:pos]) + struct.pack("<i", count + new) + bytes(data[pos + 4:at]) + bytes(extra) + bytes(data[at:])

    patched = _rewrite(raw, change)
    if not added:
        return None, resolved
    if list_keys(patched) != dict(list_keys(raw), **added):
        raise BundleError("수정한 번들 확인 실패")
    return patched, resolved


def set_strings(raw, name, texts):
    """문자열 표 번들에서 이름이 name인 표(예: Abilities_ko)에 {id: 글}을 넣음. (새 번들 바이트, 바뀐 id들) 반환.

    표에 있는 id는 글만 바꾸고 없는 id는 새 항목으로 추가. 이미 같은 글이면 건드리지 않음.
    바뀐 것이 없으면 (None, []).
    """
    changed = []

    def change(root, data):
        found = _string_table(root)
        if not found or _text(data, _offset(root, data, found[0])) != name.encode("utf-8"):
            return None
        _, table, element = found
        pos = _offset(root, data, table)
        (count,) = struct.unpack_from("<i", data, pos)
        body, at, seen = bytearray(), pos + 4, set()
        for _ in range(count):
            (entry_id,) = struct.unpack_from("<q", data, at)
            end, after_text = _skip(element, data, at), _skip(element.children[1], data, at + 8)
            wanted = texts.get(entry_id)
            seen.add(entry_id)
            if wanted is not None and wanted.encode("utf-8") != _text(data, at + 8):
                body += struct.pack("<q", entry_id) + _packed(wanted.encode("utf-8")) + bytes(data[after_text:end])
                changed.append(entry_id)
            else:
                body += data[at:end]
            at = end
        new = [entry_id for entry_id in texts if entry_id not in seen]
        for entry_id in new:
            body += struct.pack("<q", entry_id) + _packed(texts[entry_id].encode("utf-8")) + struct.pack("<i", 0)
        changed.extend(new)
        if not changed:
            return None
        return bytes(data[:pos]) + struct.pack("<i", count + len(new)) + bytes(body) + bytes(data[at:])

    patched = _rewrite(raw, change)
    if not changed:
        return None, []
    if list_strings(patched, name) != {**list_strings(raw, name), **{i: texts[i] for i in changed}}:
        raise BundleError("수정한 번들 확인 실패")
    return patched, changed


def list_strings(raw, name):
    """문자열 표 번들에서 이름이 name인 표의 {id: 글} (검증·테스트용)."""
    strings = {}

    def read(root, data):
        found = _string_table(root)
        if found and _text(data, _offset(root, data, found[0])) == name.encode("utf-8"):
            _, table, element = found
            pos = _offset(root, data, table)
            (count,) = struct.unpack_from("<i", data, pos)
            pos += 4
            for _ in range(count):
                (entry_id,) = struct.unpack_from("<q", data, pos)
                strings[entry_id] = _text(data, pos + 8).decode("utf-8")
                pos = _skip(element, data, pos)

    _rewrite(raw, read)
    return strings


def list_keys(raw):
    """번들 안 모든 키 테이블의 {키 이름: id} (검증·테스트용)."""
    bundle = Bundle(raw)
    keys = {}
    for index, (_, _, _, name) in enumerate(bundle.nodes):
        if name.endswith((".resS", ".resource")):
            continue
        assets = SerializedFile(bundle.file(index))
        for number, (_, _, _, type_index) in enumerate(assets.objects):
            class_id, root = assets.types[type_index]
            table = _key_table(root) if class_id == MONO_BEHAVIOUR else None
            if not table:
                continue
            entries, element, _ = table
            data = assets.object_bytes(number)
            pos = 0
            for child in root.children:
                if child is entries:
                    break
                pos = _skip(child, data, pos)
            (count,) = struct.unpack_from("<i", data, pos)
            pos += 4
            for _ in range(count):
                key_id, length = struct.unpack_from("<qi", data, pos)
                keys[bytes(data[pos + 12:pos + 12 + length]).decode("utf-8")] = key_id
                pos = _skip(element, data, pos)
    return keys
