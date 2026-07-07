#!/usr/bin/env python3
"""
rss-keyword-filter / ops_monitor_keyword.py

监控告警模块：检测 RSS 源更新停滞、连续运行失败等异常，
通过 raft CLI 向频道发送告警通知。

配置来源：
- ops_config.json：部署参数（路径、告警阈值等）
- rss_sources.json：源列表和 per-source stale_threshold_hours（含全局默认值）

用法：python3 ops_monitor_keyword.py
      RAFT_CLI=raft python3 ops_monitor_keyword.py
"""

import json
import os
import subprocess
import sys
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ops_monitor")

RAFT_CLI = os.environ.get("RAFT_CLI", "raft")

DEFAULT_CONFIG = {
    "sources_config": "rss_sources.json",
    "state_file": "state.json",
    "runner_status_file": "runner_status.json",
    "alert_state_file": "alert_state.json",
    "max_consecutive_failures": 3,
}


def load_config(path="ops_config.json"):
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                override = json.load(f)
            config.update(override)
            log.info("Loaded config from %s", path)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load %s: %s", path, e)
    return config


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def send_alert(message):
    try:
        result = subprocess.run(
            [RAFT_CLI, "message", "send", "--target", "#keyword-filter"],
            input=message, text=True, capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            log.info("Alert sent")
        else:
            log.error("Failed to send alert: %s", result.stderr.strip()[:100])
    except Exception as e:
        log.error("Failed to send alert: %s", e)


def check_staleness(config):
    """检查各源更新停滞情况。源信息从 rss_sources.json 读取。"""
    state = load_json(config["state_file"])
    if not state or "last_entry_times" not in state:
        return []

    src_config = load_json(config["sources_config"]) or {}
    default_stale = src_config.get("stale_threshold_hours", 24)
    sources_list = src_config.get("sources", [])

    # 构建 id → per-source 配置映射
    source_info = {}
    for src in sources_list:
        sid = src.get("id", "")
        if not sid:
            import hashlib
            sid = "src_" + hashlib.sha256(src.get("url", "").encode()).hexdigest()[:12]
        source_info[sid] = {
            "name": src.get("name", sid),
            "threshold": src.get("stale_threshold_hours", default_stale),
        }

    now = datetime.now(timezone(timedelta(hours=8)))
    stale = []

    for src_id, entry_time_str in state.get("last_entry_times", {}).items():
        try:
            entry_time = datetime.fromisoformat(entry_time_str)
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone(timedelta(hours=8)))
            elapsed = now - entry_time
            info = source_info.get(src_id, {"name": src_id, "threshold": default_stale})
            if elapsed.total_seconds() > info["threshold"] * 3600:
                stale.append({
                    "source_id": src_id,
                    "name": info["name"],
                    "last_entry": entry_time_str,
                    "hours_since": elapsed.total_seconds() / 3600,
                    "threshold": info["threshold"],
                })
        except (ValueError, TypeError):
            continue
    return stale


def check_runner_failures(config):
    status = load_json(config["runner_status_file"])
    if not status:
        return None
    failures = status.get("consecutive_failures", 0)
    if failures >= config["max_consecutive_failures"]:
        return failures
    return None


def main():
    config = load_config()
    log.info("=== Ops Monitor: keyword-filter ===")

    alerts = []

    stale = check_staleness(config)
    for s in stale:
        msg = (
            f"🚨 keyword-filter 源停滞告警\n"
            f"- **{s['name']}**：已 {s['hours_since']:.1f}h > 阈值{s['threshold']}h\n"
            f"  最后条目：{s['last_entry']}"
        )
        alerts.append(msg)

    failures = check_runner_failures(config)
    if failures:
        msg = f"🚨 keyword-filter 连续 {failures} 次运行失败"
        alerts.append(msg)

    if alerts:
        for alert in alerts:
            send_alert(alert)
    else:
        log.info("All sources healthy, no alerts.")

    # 记录检查结果
    alert_state = load_json(config["alert_state_file"]) or {"checks": []}
    alert_state["checks"].append({
        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "alerts_count": len(alerts),
    })
    alert_state["checks"] = alert_state["checks"][-100:]
    with open(config["alert_state_file"], "w", encoding="utf-8") as f:
        json.dump(alert_state, f, ensure_ascii=False, indent=2)

    return 0 if not alerts else 1


if __name__ == "__main__":
    sys.exit(main())
