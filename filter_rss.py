#!/usr/bin/env python3
"""
rss-keyword-filter / filter_rss.py

从 RSS 源配置中读取用户指定的源列表和各自的关键词规则，
对每一条目进行关键词匹配，输出 hit / maybe / ignore 三级决策。

配置格式见 README.md 或 rss_sources.example.json。

脚本不假设任何源的存在、数量或规则内容——完全由配置驱动。
任何人拿到此代码，只需写自己的 rss_sources.json 即可使用。

依赖：Python 标准库（urllib, xml.etree.ElementTree, json）
"""

import json
import hashlib
import logging
import os
import re
import sys
import argparse
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("filter_rss")


# ── 配置加载 ────────────────────────────────────────────────


def load_sources(path="rss_sources.json"):
    """加载源配置文件，返回 (global_defaults, sources_list)"""
    if not os.path.exists(path):
        log.error("Config file not found: %s", path)
        log.error("Copy rss_sources.example.json to %s and configure your sources.", path)
        return {}, []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load %s: %s", path, e)
        return {}, []

    defaults = {"stale_threshold_hours": 24}
    if "stale_threshold_hours" in data:
        defaults["stale_threshold_hours"] = data["stale_threshold_hours"]

    sources = data.get("sources", [])
    log.info("Loaded %d source(s) from %s", len(sources), path)
    return defaults, sources


def resolve_id(src):
    """生成稳定 ID：用户指定，或自动从 URL hash 生成 src_<sha256_12>"""
    if src.get("id"):
        return src["id"]
    url = src.get("url", "")
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"src_{h}"


def resolve_rules(src, global_defaults):
    """返回该源的 rules 字段（仅 per-source，无全局 fallback）。"""
    return src.get("rules", {})


# ── RSS 抓取 ────────────────────────────────────────────────


def fetch_rss(url, timeout=30):
    """抓取并解析 RSS feed"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; RSSKeywordFilter/1.0; "
            "+https://github.com/IceCodeNew/rss-keyword-filter)"
        )
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (URLError, HTTPError, OSError) as e:
        log.error("Failed to fetch %s: %s", url, e)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        log.error("XML parse error for %s: %s", url, e)
        return []

    items = []
    for item in root.iter("item"):
        entry = _parse_rss_item(item)
        if entry:
            items.append(entry)

    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            entry_data = _parse_atom_entry(entry)
            if entry_data:
                items.append(entry_data)

    return items


def _parse_rss_item(item):
    title = _get_text(item, "title", "")
    link = _get_text(item, "link", "")
    guid = _get_text(item, "guid", link)
    pubdate_str = _get_text(item, "pubDate", "")
    content = _get_text(item, "description", _get_text(item, "content:encoded", ""))
    pubdate = _parse_date(pubdate_str)
    return {"title": title.strip(), "link": link.strip(), "guid": guid.strip(),
            "published": pubdate, "content": content.strip()[:500]}


def _parse_atom_entry(entry):
    NS = "{http://www.w3.org/2005/Atom}"
    title = _get_text(entry, NS + "title", "")
    link_el = entry.find(NS + "link")
    link = link_el.get("href", "") if link_el is not None else ""
    entry_id = _get_text(entry, NS + "id", link)
    updated = _get_text(entry, NS + "updated", "")
    content = _get_text(entry, NS + "content", _get_text(entry, NS + "summary", ""))
    pubdate = _parse_date(updated)
    return {"title": title.strip(), "link": link.strip(), "guid": entry_id.strip(),
            "published": pubdate, "content": content.strip()[:500]}


def _get_text(parent, tag, default=""):
    el = parent.find(tag)
    return el.text.strip() if el is not None and el.text else default


def _parse_date(date_str):
    if not date_str:
        return None
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            return datetime.strptime(date_str.strip(), fmt).astimezone(
                timezone(timedelta(hours=8))
            )
        except ValueError:
            continue
    return None


def to_beijing(dt):
    if dt is None:
        return "未知"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── 关键词匹配 ──────────────────────────────────────────────


def match_rules(item, rules):
    """对单一条目执行关键词匹配。

    命中逻辑：
      1. exclude_keywords 任一命中 → ignore（立即返回）
      2. hit_keywords 任一命中 → hit，score += 10
      3. maybe_keywords 命中 → score += 5
      4. score >= 10 → hit；score >= 5 → maybe；else → ignore
      5. ignore_patterns.title/content 匹配 → ignore
    """
    title = item["title"]
    content = item["content"]
    text = title + " " + content

    hit_kw = rules.get("hit_keywords", [])
    maybe_kw = rules.get("maybe_keywords", [])
    exclude_kw = rules.get("exclude_keywords", [])
    ignore_patterns = rules.get("ignore_patterns", {})

    # 1. 排除词
    for kw in exclude_kw:
        if kw in text:
            return 0, [], f"排除词「{kw}」命中", "ignore"

    # 2-3. 命中词 & 可能词
    score = 0
    matched = []
    hints = []

    for kw in hit_kw:
        if kw in text:
            score += 10
            matched.append(kw)
            hints.append(f"命中词「{kw}」+10")

    for kw in maybe_kw:
        if kw in text:
            score += 5
            matched.append(kw)
            hints.append(f"可能词「{kw}」+5")

    # 4. 误报排除
    for pat in ignore_patterns.get("title", []):
        if re.search(pat, title):
            return 0, [], f"标题排除:匹配「{pat}」", "ignore"
    for pat in ignore_patterns.get("content", []):
        if re.search(pat, content):
            return 0, [], f"正文排除:匹配「{pat}」", "ignore"

    # 5. 决策
    if score >= 10:
        decision = "hit"
    elif score >= 5:
        decision = "maybe"
    else:
        return 0, [], "score 不足 5", "ignore"

    summary = "；".join(hints[:5])
    return score, matched, summary, decision


# ── 状态管理 ────────────────────────────────────────────────


def load_state(state_path="state.json"):
    """加载状态文件，返回 (seen_set, last_entry_times_dict)"""
    if not os.path.exists(state_path):
        return set(), {}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        seen = set(data.get("seen", []))
        last_times = data.get("last_entry_times", {})
        return seen, last_times
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to load state file, starting fresh.")
        return set(), {}


def save_state(seen_guids, last_entry_times, state_path="state.json"):
    """保存去重状态和每个源的最后条目时间。"""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({
            "seen": list(seen_guids),
            "last_entry_times": last_entry_times,
            "updated": datetime.now().isoformat(),
        }, f, ensure_ascii=False)


# ── 主入口 ──────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="RSS 强时效信息筛选（数据驱动）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--commit", action="store_true", help="写入去重状态")
    parser.add_argument("--lookback-days", type=int, default=1, help="回溯天数")
    parser.add_argument("--include-seen", action="store_true", help="包含历史条目")
    parser.add_argument("--config", default="rss_sources.json", help="源配置文件")
    args = parser.parse_args()

    global_defaults, sources = load_sources(args.config)
    if not sources:
        log.error("No sources configured. Exiting.")
        return 1

    cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=args.lookback_days)
    log.info("Lookback: %s ~ now (%dd)", cutoff.strftime("%Y-%m-%d %H:%M"), args.lookback_days)

    state_path = "state.json"
    seen, last_entry_times = load_state(state_path) if not args.include_seen else (set(), {})
    log.info("Seen GUIDs: %d, tracked sources: %d", len(seen), len(last_entry_times))

    all_results = []
    stats = {"hit": 0, "maybe": 0, "ignore": 0}

    for src in sources:
        src_id = resolve_id(src)
        name = src.get("name", src_id)
        url = src.get("url", "")
        if not url:
            log.warning("Source '%s' has no URL, skipping.", src_id)
            continue

        rules = resolve_rules(src, global_defaults)

        log.info("Fetching %s ...", name)
        items = fetch_rss(url)
        log.info("  %d items from %s", len(items), src_id)

        # 追踪该源本次最新条目时间
        src_latest = last_entry_times.get(src_id, "")

        for item in items:
            if item.get("published") and item["published"] < cutoff:
                continue
            if item["guid"] in seen:
                continue

            score, matched, hint, decision = match_rules(item, rules)

            # 更新该源的最晚条目时间
            pub_str = to_beijing(item.get("published"))
            if pub_str and pub_str != "未知" and pub_str > src_latest:
                src_latest = pub_str

            result = {
                "source": src_id,
                "title": item["title"],
                "link": item["link"],
                "published_beijing": pub_str,
                "guid": item["guid"],
                "score": score,
                "matched_rules": matched,
                "summary_hint": hint,
                "decision": decision,
            }
            all_results.append(result)
            stats[decision] += 1

        # 更新该源的最后条目时间（即使没有新条目，保留旧值）
        if src_latest:
            last_entry_times[src_id] = src_latest

    output = {
        "sources_configured": len(sources),
        "total_items": len(all_results),
        "hit": stats["hit"],
        "maybe": stats["maybe"],
        "ignore": stats["ignore"],
        "items": all_results,
    }

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"\n=== 摘要 ===")
        print(f"源: {len(sources)} | 处理: {len(all_results)} | "
              f"hit={stats['hit']} maybe={stats['maybe']} ignore={stats['ignore']}")
        for r in all_results:
            if r["decision"] in ("hit", "maybe"):
                print(f"  [{r['decision']:>5}] [{r['source']}] "
                      f"score={r['score']} {r['title'][:60]}")
                print(f"         → {r['summary_hint']}")

    if args.commit:
        new_guids = set()
        for r in all_results:
            if r["decision"] in ("hit", "maybe"):
                new_guids.add(r["guid"])
        seen.update(new_guids)
        save_state(seen, last_entry_times, state_path)
        log.info("Committed %d new GUIDs (total: %d, sources tracked: %d)",
                 len(new_guids), len(seen), len(last_entry_times))

    return 0


if __name__ == "__main__":
    sys.exit(main())
