# RSS Keyword Filter

对 RSS 源中的文章进行关键词匹配，筛选强时效信息。

用户通过配置文件提供 RSS 源列表（URL、关键词规则等），
脚本遍历每个源、解析文章、执行关键词匹配，输出 hit / maybe / ignore 三级结果。

> **设计原则**：本代码不假设任何特定来源或规则。
> 所有源和规则由用户配置驱动，加/删源只改配置，不改代码。

## 快速开始

### 依赖

- Python 3.8+
- 标准库（无需第三方包）

### 首次部署

```bash
# 1. 克隆仓库
git clone <repo-url>
cd rss-keyword-filter

# 2. 配置 RSS 源和规则
cp rss_sources.example.json rss_sources.json
# 编辑 rss_sources.json，填入真实源 URL 和关键词

# 3. 验证语法
python3 -m py_compile filter_rss.py

# 4. dry-run 测试
python3 filter_rss.py --json --lookback-days 1

# 5. 首次提交（写入去重状态）
python3 filter_rss.py --commit --lookback-days 1
```

### 日常运行

```bash
# 小时级自动运行
python3 run_hourly_keyword.py

# dry-run 最近 N 天
python3 filter_rss.py --json --lookback-days 3
```

## 配置格式

编辑 `rss_sources.json`（参考 `rss_sources.example.json`）：

```json
{
  "stale_threshold_hours": 24,
  "sources": [
    {
      "id": "auto-generated-or-custom",
      "name": "显示名称",
      "url": "https://example.com/feed.xml",
      "lang": "zh",
      "rules": {
        "hit_keywords": ["预购", "抽选", "开票", "报名"],
        "maybe_keywords": ["活动", "限时", "今日"],
        "exclude_keywords": [],
        "ignore_patterns": {
          "title": ["回顾|就职|招聘"],
          "content": ["长期|每[周日月]"]
        }
      },
      "stale_threshold_hours": 48
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `stale_threshold_hours` | 全局默认停滞告警阈值（小时），per-source 可覆盖 |
| `sources[].id` | 可选；不填则按 URL hash 自动生成 "src_<hash>" |
| `sources[].name` | 展示用名称（告警、输出中使用） |
| `sources[].url` | RSS feed URL |
| `sources[].lang` | 语言标记（`zh` 或 `ja`），用于展示/扩展 |
| `sources[].rules.hit_keywords` | 命中即判定为 hit 的关键词 |
| `sources[].rules.maybe_keywords` | 匹配后累积计分，达到阈值升为 hit |
| `sources[].rules.exclude_keywords` | 匹配则直接判定为 ignore |
| `sources[].rules.ignore_patterns.title` | 标题匹配则忽略（正则） |
| `sources[].rules.ignore_patterns.content` | 正文匹配则忽略（正则） |
| `sources[].stale_threshold_hours` | 覆盖全局默认值 |

### 命中逻辑

1. **exclude_keywords**：任一匹配 → ignore（不继续匹配）
2. **hit_keywords**：任一匹配 → hit（score += 10）
3. **maybe_keywords**：匹配后 score += 5
4. **total score ≥ 10** → hit；**≥ 5** → maybe；**< 5** → ignore

## 项目结构

```
rss-keyword-filter/
├── .gitignore                 # 忽略状态文件、生产配置、日志
├── README.md                  # 本文件
├── filter_rss.py              # RSS 采集 + 关键词筛选
├── run_hourly_keyword.py      # 小时级运行器
├── ops_monitor_keyword.py     # 监控告警（停滞检测、失败告警）
├── ops_config.example.json    # 运行配置模板
└── rss_sources.example.json   # 源配置示例（1 个假源）
```

## `filter_rss.py` CLI

```
usage: filter_rss.py [--json] [--commit] [--lookback-days N]
                     [--include-seen] [--config PATH]

  --json              输出 JSON 到 stdout
  --commit            写入去重状态
  --lookback-days N   回溯天数（默认 1）
  --include-seen      包含已处理条目
  --config PATH       源配置文件路径
```

## 运行监控

`ops_monitor_keyword.py` 检测：
- 各源停滞时间超过 `stale_threshold_hours`（从 rss_sources.json 读取）
- 连续运行失败（默认 3 次触发告警）
- 环境变量 `RAFT_CLI` 指定 raft CLI 路径
