#!/usr/bin/env python3
"""
test_filter_rss.py — 单元测试

覆盖：
- 配置加载 (load_sources)
- ID 生成 (resolve_id)
- 关键词匹配 (match_rules)：hit / maybe / ignore / exclude / ignore_patterns
- 状态管理 (load_state / save_state)
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

# 把项目根加入 Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from filter_rss import (
    load_sources,
    resolve_id,
    resolve_rules,
    match_rules,
    load_state,
    save_state,
    to_beijing,
)


class TestLoadSources(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def _write_config(self, data):
        path = os.path.join(self.tmpdir, "rss_sources.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_missing_file(self):
        defaults, sources = load_sources("/nonexistent/path.json")
        self.assertEqual(defaults, {})
        self.assertEqual(sources, [])

    def test_empty_sources(self):
        path = self._write_config({"sources": []})
        defaults, sources = load_sources(path)
        self.assertEqual(defaults.get("stale_threshold_hours"), 24)
        self.assertEqual(sources, [])

    def test_single_source(self):
        cfg = {
            "stale_threshold_hours": 48,
            "sources": [
                {
                    "id": "my-feed",
                    "name": "My Feed",
                    "url": "https://example.com/feed.xml",
                    "lang": "zh",
                    "rules": {"hit_keywords": ["预购"]},
                }
            ],
        }
        path = self._write_config(cfg)
        defaults, sources = load_sources(path)
        self.assertEqual(defaults["stale_threshold_hours"], 48)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["id"], "my-feed")

    def test_global_default_stale(self):
        cfg = {"sources": [{"url": "https://example.com/feed.xml"}]}
        path = self._write_config(cfg)
        defaults, sources = load_sources(path)
        self.assertEqual(defaults["stale_threshold_hours"], 24)


class TestResolveId(unittest.TestCase):
    def test_custom_id(self):
        src = {"id": "my-source", "url": "https://example.com/feed.xml"}
        self.assertEqual(resolve_id(src), "my-source")

    def test_auto_generated_hash(self):
        src = {"url": "https://example.com/feed.xml"}
        sid = resolve_id(src)
        self.assertTrue(sid.startswith("src_"))
        self.assertEqual(len(sid), 16)  # "src_" + 12 chars

    def test_same_url_same_id(self):
        src1 = {"url": "https://example.com/feed.xml"}
        src2 = {"url": "https://example.com/feed.xml"}
        self.assertEqual(resolve_id(src1), resolve_id(src2))

    def test_different_url_different_id(self):
        src1 = {"url": "https://example.com/feed1.xml"}
        src2 = {"url": "https://example.com/feed2.xml"}
        self.assertNotEqual(resolve_id(src1), resolve_id(src2))


class TestResolveRules(unittest.TestCase):
    def test_returns_rules_field(self):
        src = {"rules": {"hit_keywords": ["test"]}}
        self.assertEqual(resolve_rules(src, {}), {"hit_keywords": ["test"]})

    def test_no_rules_returns_empty_dict(self):
        self.assertEqual(resolve_rules({}, {}), {})

    def test_no_ignore_titles_compat(self):
        """确保没有 ignore_titles 兼容逻辑的历史债务"""
        src = {"rules": {"hit_keywords": ["test"]}}
        rules = resolve_rules(src, {})
        self.assertNotIn("ignore_titles", rules)


class TestMatchRules(unittest.TestCase):
    def setUp(self):
        self.item = {
            "title": "今日预购开始",
            "content": "限量发售，名额有限",
        }

    def test_hit_keyword_hit(self):
        rules = {"hit_keywords": ["预购"]}
        score, matched, hint, decision = match_rules(self.item, rules)
        self.assertEqual(decision, "hit")

    def test_maybe_keyword_maybe(self):
        item = {"title": "活动预告", "content": "详情请关注后续通知"}
        rules = {"maybe_keywords": ["活动预告"]}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "maybe")

    def test_exclude_keyword_ignore(self):
        rules = {"hit_keywords": ["预购"], "exclude_keywords": ["回顾"]}
        item = {"title": "预购回顾", "content": "上月活动总结"}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "ignore")

    def test_ignore_patterns_title(self):
        rules = {
            "hit_keywords": ["报名"],
            "ignore_patterns": {"title": ["回顾|招聘"]},
        }
        item = {"title": "招聘报名通知", "content": "详情请联系"}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "ignore")

    def test_ignore_patterns_content(self):
        rules = {
            "hit_keywords": ["活动"],
            "ignore_patterns": {"content": ["毎週|每周"]},
        }
        item = {"title": "活动通知", "content": "每周三下午举行"}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "ignore")

    def test_no_match_ignore(self):
        rules = {"hit_keywords": ["抽选"], "maybe_keywords": ["限时"]}
        item = {"title": "天气预报", "content": "今日晴转多云"}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "ignore")

    def test_score_10_hit(self):
        """两个 maybe 词累积到 score >= 10"""
        rules = {"maybe_keywords": ["活动", "限时"]}
        item = {"title": "限时活动", "content": "本周末"}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "hit")

    def test_exclude_overrides_hit(self):
        """exclude 优先级高于 hit"""
        rules = {
            "hit_keywords": ["发售"],
            "exclude_keywords": ["回顾"],
        }
        item = {"title": "发售回顾", "content": ""}
        score, matched, hint, decision = match_rules(item, rules)
        self.assertEqual(decision, "ignore")

    def test_empty_rules(self):
        item = {"title": "任何内容", "content": ""}
        score, matched, hint, decision = match_rules(item, {})
        self.assertEqual(decision, "ignore")

    def test_matched_rules_field(self):
        rules = {"hit_keywords": ["预购"], "maybe_keywords": ["今日"]}
        score, matched, hint, decision = match_rules(self.item, rules)
        self.assertIn("预购", matched)
        # 只有 hit_keywords "预购" 命中了
        # "今日" 在 maybe_keywords 中，但标题有"今日" -> 应该也在 matched 中
        # 标题是"今日预购开始" -> "今日" 和 "预购" 都在 text 中


class TestStateManagement(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.tmpdir, "state.json")

    def test_load_empty(self):
        seen, times = load_state(self.state_path)
        self.assertEqual(seen, set())
        self.assertEqual(times, {})

    def test_save_and_load(self):
        save_state({"guid1", "guid2"}, {"src-a": "2026-07-07 10:00:00"}, self.state_path)
        seen, times = load_state(self.state_path)
        self.assertEqual(seen, {"guid1", "guid2"})
        self.assertEqual(times["src-a"], "2026-07-07 10:00:00")

    def test_save_overwrite(self):
        save_state({"guid1"}, {"src-a": "2026-07-07 10:00:00"}, self.state_path)
        save_state({"guid1", "guid2"}, {"src-a": "2026-07-07 12:00:00", "src-b": "2026-07-07 11:00:00"}, self.state_path)
        seen, times = load_state(self.state_path)
        self.assertEqual(seen, {"guid1", "guid2"})
        self.assertEqual(len(times), 2)

    def test_corrupted_file(self):
        with open(self.state_path, "w") as f:
            f.write("{corrupted")
        seen, times = load_state(self.state_path)
        self.assertEqual(seen, set())
        self.assertEqual(times, {})


class TestToBeijing(unittest.TestCase):
    def test_none(self):
        self.assertEqual(to_beijing(None), "未知")

    def test_datetime(self):
        dt = datetime(2026, 7, 7, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(to_beijing(dt), "2026-07-07 10:00:00")

    def test_utc_conversion(self):
        """_parse_date 返回的 datetime 已是北京时间，strftime 直接格式化。"""
        from filter_rss import _parse_date
        # 模拟 RSS 中的 UTC 时间
        dt = _parse_date("Tue, 07 Jul 2026 02:00:00 +0000")
        self.assertIsNotNone(dt)
        self.assertEqual(to_beijing(dt), "2026-07-07 10:00:00")


if __name__ == "__main__":
    unittest.main()
