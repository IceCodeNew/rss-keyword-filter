#!/usr/bin/env python3
"""
rss-keyword-filter / run_hourly_keyword.py

小时级运行器：调用 filter_rss.py --commit 执行采集和筛选，
连续失败时触发频道告警。

用法：python3 run_hourly_keyword.py
      RAFT_CLI=raft python3 run_hourly_keyword.py

此脚本不读取源配置，不假设任何源存在。
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
log = logging.getLogger("run_hourly")

STATE_FILE = "runner_status.json"
MAX_FAILURES = 3


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config(config_path="ops_config.json"):
    """加载部署配置。"""
    defaults = {
        "sources_config": "rss_sources.json",
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                override = json.load(f)
            defaults.update(override)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load %s: %s", config_path, e)
    return defaults


def run_filter(sources_config="rss_sources.json"):
    cmd = [sys.executable, "filter_rss.py", "--commit", "--lookback-days", "1", "--config", sources_config]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return result.returncode == 0, result.stdout, result.stderr


def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    log.info("=== Keyword Filter Hourly Run ===")

    config = load_config()
    sources_config = config.get("sources_config", "rss_sources.json")

    success, stdout, stderr = run_filter(sources_config)

    status = load_json(STATE_FILE, {"consecutive_failures": 0, "last_run": None, "last_success": None})
    status["last_run"] = now.isoformat()

    if success:
        log.info("filter_rss.py completed successfully")
        status["consecutive_failures"] = 0
        status["last_success"] = now.isoformat()
        for line in stdout.splitlines():
            if "hit" in line.lower() or "maybe" in line.lower():
                log.info("  %s", line.strip())
    else:
        status["consecutive_failures"] = status.get("consecutive_failures", 0) + 1
        log.error("filter_rss.py failed (%d/%d)",
                  status["consecutive_failures"], MAX_FAILURES)
        if stderr:
            log.error("stderr: %s", stderr.strip()[:200])

    save_json(STATE_FILE, status)

    if status["consecutive_failures"] >= MAX_FAILURES:
        raft_cli = os.environ.get("RAFT_CLI", "raft")
        msg = (
            f"🚨 keyword-filter 连续 {status['consecutive_failures']} 次运行失败\n"
            f"时间：{now.strftime('%Y-%m-%d %H:%M')}"
        )
        try:
            subprocess.run(
                [raft_cli, "message", "send", "--target", "#keyword-filter"],
                input=msg, text=True, capture_output=True, timeout=30,
            )
        except Exception as e:
            log.error("Failed to send alert: %s", e)
    else:
        log.info("No alerts triggered.")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
