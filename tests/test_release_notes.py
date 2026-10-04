import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'patcher') if (ROOT / 'patcher').is_dir() else str(ROOT))

import patcher

BODY = """# v1.0.4 폭압의 렌즈 설명 수정
## 📖 주요 작업
- **폭압의 렌즈** 설명을 수정했습니다
  - 이전: 호의 200% 소모

## ⚠️ Windows Defender가 패처를 차단하는 경우
- 오탐입니다

---
"""


def release(tag, body=BODY, **extra):
    return {"tag_name": tag, "name": f"{tag} 제목", "published_at": "2026-10-03T04:50:52Z", "body": body, **extra}


class ReleaseNotesTest(unittest.TestCase):
    def test_highlights_keep_only_first_section(self):
        self.assertEqual(patcher.release_highlights(BODY),
                         ["- **폭압의 렌즈** 설명을 수정했습니다", "  - 이전: 호의 200% 소모"])

    def test_highlights_without_sections_use_whole_body(self):
        self.assertEqual(patcher.release_highlights("# 제목\r\n첫 줄\r\n\r\n둘째 줄\r\n---\r\n"), ["첫 줄", "", "둘째 줄"])
        self.assertEqual(patcher.release_highlights(None), [])

    def test_history_lists_all_releases_and_marks_newer_than_installed(self):
        releases = [release("v1.0.2"), release("v1.0.4"), release("v1.0.3"), release("v1.0.5", draft=True),
                    release("v1.1.0-beta", prerelease=True)]
        history = patcher.release_history(releases, "v1.0.2")
        self.assertEqual([(e["tag"], e["new"]) for e in history], [("v1.0.4", True), ("v1.0.3", True), ("v1.0.2", False)])
        self.assertEqual(history[0]["date"], "2026-10-03")

    def test_history_has_nothing_new_when_current_or_not_installed(self):
        releases = [release("v1.0.3"), release("v1.0.4")]
        for current in ("v1.0.4", None):
            self.assertEqual([(e["tag"], e["new"]) for e in patcher.release_history(releases, current)],
                             [("v1.0.4", False), ("v1.0.3", False)])
        self.assertEqual(patcher.release_history([], "v1.0.4"), [])

    def test_history_ignores_patcher_package_releases(self):
        releases = [release("v1.0.3"), release("v1.0.4"), release("patcher-v9.0.0")]
        self.assertEqual([e["tag"] for e in patcher.release_history(releases, "v1.0.3")], ["v1.0.4", "v1.0.3"])

    def test_history_starts_at_v1_0_0(self):
        releases = [release("v0.8.0"), release("0.6.2"), release("v1.0.0"), release("v1.0.1")]
        self.assertEqual([e["tag"] for e in patcher.release_history(releases, "v0.8.0")], ["v1.0.1", "v1.0.0"])

    def test_markdown_spans(self):
        self.assertEqual(patcher.markdown_spans("- **굵게** 보통 `코드`"), ("• ", [("굵게", True), (" 보통 코드", False)]))
        self.assertEqual(patcher.markdown_spans("  - [링크](https://example.com) 글"), ("• ", [("링크 글", False)]))
        self.assertEqual(patcher.markdown_spans("그냥 글"), ("", [("그냥 글", False)]))


if __name__ == "__main__":
    unittest.main()
