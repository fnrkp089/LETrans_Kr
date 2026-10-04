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

    def test_changelog_lists_releases_newer_than_installed(self):
        releases = [release("v1.0.2"), release("v1.0.4"), release("v1.0.3"), release("v1.0.5", draft=True),
                    release("v1.1.0-beta", prerelease=True)]
        self.assertEqual([e["tag"] for e in patcher.changelog_since(releases, "v1.0.2")], ["v1.0.4", "v1.0.3"])
        self.assertEqual(patcher.changelog_since(releases, "v1.0.2")[0]["date"], "2026-10-03")

    def test_changelog_shows_latest_when_current_or_not_installed(self):
        releases = [release("v1.0.3"), release("v1.0.4")]
        self.assertEqual([e["tag"] for e in patcher.changelog_since(releases, "v1.0.4")], ["v1.0.4"])
        self.assertEqual([e["tag"] for e in patcher.changelog_since(releases, None)], ["v1.0.4"])
        self.assertEqual(patcher.changelog_since([], "v1.0.4"), [])

    def test_changelog_ignores_patcher_package_releases(self):
        releases = [release("v1.0.3"), release("v1.0.4"), release("patcher-v9.0.0")]
        self.assertEqual([e["tag"] for e in patcher.changelog_since(releases, "v1.0.3")], ["v1.0.4"])
        self.assertEqual([e["tag"] for e in patcher.changelog_since(releases, None)], ["v1.0.4"])

    def test_changelog_limit(self):
        releases = [release(f"v1.0.{i}") for i in range(1, 10)]
        self.assertEqual(len(patcher.changelog_since(releases, "v1.0.0", limit=3)), 3)

    def test_markdown_spans(self):
        self.assertEqual(patcher.markdown_spans("- **굵게** 보통 `코드`"), ("• ", [("굵게", True), (" 보통 코드", False)]))
        self.assertEqual(patcher.markdown_spans("  - [링크](https://example.com) 글"), ("• ", [("링크 글", False)]))
        self.assertEqual(patcher.markdown_spans("그냥 글"), ("", [("그냥 글", False)]))


if __name__ == "__main__":
    unittest.main()
