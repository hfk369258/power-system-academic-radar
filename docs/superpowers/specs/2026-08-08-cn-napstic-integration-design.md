# 中文文献源（NAPSTIC）整合设计

日期：2026-08-08
状态：已确认（用户已认可方案与配置项）

## 背景与目标

`power-system-academic-radar` 目前只有英文数据源（OpenAlex、Crossref、arXiv、Semantic Scholar、IEEE Xplore、Elsevier/Scopus、RSS、手工导出），无法检索中文期刊文献。本机已有的 Claude Code 插件 `cn-abstracts-tool`（`cn_napstic.py`）实现了从「国家学术搜索」(NAPSTIC, ISTIC 运营) 抓取 11 本电力系统核心期刊的题录与摘要，输出统一 schema。

目标：把该插件的两个数据通道（关键词检索 `search`、期刊逐期 `recent`）整合为雷达的原生数据源，使中文文献与英文文献走同一条 抓取 → 筛选 → 去重 → DeepSeek 解读 → 邮件推送 链路，并通过控制台统一开关。整合完成后提交并推送到 GitHub 仓库。

SMTP 实测配置：发件 `sender@qq.example.com`（QQ 邮箱，需授权码），收件 `target@zhou.example.com`。

## 非目标（范围外）

- 不新增"万方 gRPC"通道（`万方补充源说明.md` 中的进阶方案，接口未公开、有风险，留待后续）。
- 不接知网/维普（法律风险与反爬，已排除）。
- 不修改控制台 UI 主干（现有通用数据源渲染即可开关、调候选量；期刊列表等专项配置在 JSON 中维护）。
- 不新增英文姊妹刊数据源（已有 IEEE/Springer 链路可覆盖，仅补文档说明）。

## 架构总览

```
config.json (sources 新增两条)
        │
        ▼
fetch_enabled_sources()  ── 分发器（power_system_radar.py:2278）
        │
        ├── napstic_search   → fetch_napstic_search()    → cn_napstic.search_literature()
        └── napstic_journals → fetch_napstic_journals()  → cn_napstic.fetch_recent()
                                 │
                                 ▼
                    clean_item() 字段映射（新增 abstract_en / source_id）
                                 ▼
        dedupe_and_score() → 去重键含 source_id，min_score 豁免 napstic 前缀
                                 ▼
        现有 digest / HTML / 邮件 全链路（中文本就支持）
```

## 数据流与字段映射

NAPSTIC 记录 → `clean_item()` 映射：

| NAPSTIC 字段 | clean_item 字段 | 说明 |
|---|---|---|
| `title_cn` | `title` | 主标题用中文 |
| `title_en` | `title_en`（新增） | 保留英文标题 |
| `abstract_cn` | `abstract` + `abstract_zh` | 中文摘要为主 |
| `abstract_en` | `abstract_en`（新增） | 有则保留 |
| `authors_cn` | `authors` | |
| `journal_cn` | `venue` | 中文刊名 |
| `doi` | `doi` | 跨源去重主键 |
| `detail_url` | `url` | |
| `keywords_cn` | `keywords` | |
| `source_id` | `source_id`（新增） | NAPSTIC 文章 ID，去重回退键 |
| `year` | `year` | |
| `online_date`（search 通道） | `published` | 网络首发日期，增量与排序基准 |
| 常量 | `publication_type=journal`、`venue_type=journal` | 中文期刊论文 |
| — | `source` | 取配置源条目 `name`（如 `cn_napstic_search`） |

`item_key()`（`power_system_radar.py:153`）扩展为三级回退：`doi:` → `sid:<source_id>` → `title:`。`normalized_title_key` 已支持中文（`\u4e00-\u9fff`），无需改动。

### 评分豁免

`dedupe_and_score`（`:1251`）的 `min_score` 豁免条件扩展为：`source` 以 `manual`/`napstic` 开头**或包含 `napstic`（大小写不敏感）**——注意 `clean_item` 中 `source` 保存的是配置里的中文源名（如 `中文检索(NAPSTIC)`），因此用包含判断而非前缀判断。检索通道返回的条目本身已被中文关键词检索式约束，低分豁免合理。

### 期刊白名单

`journal_filter` 启用时是硬门槛。三份模板的 `categories.chinese_ei` 需补全为 11 本（新增：中国电力、电力建设、华北电力大学学报、现代电力），保证 NAPSTIC 全部期刊可过白名单；`journal_filter_match` 为子串匹配，`华北电力大学学报(自然科学版)` 可命中 `华北电力大学学报`。

## 新抓取器

两个函数签名与现有 `fetch_*` 一致：`(config, source, since: dt.date, limit: int) -> list[dict]`。

### `fetch_napstic_search`

- 检索词：`source_query(config, source)` → `build_default_query` 的 `cnki`/`manual_chinese` 分支（`chinese` 关键词组 OR 组合），并允许 `query_override` 覆盖。`build_default_query` 需把 `napstic_search` 加入中文类型集合。
- 翻页：`pages = max(1, ceil(limit / size))`，size 取 `source.size`（默认 20）。
- 增量过滤：`online_date >= since`（字符串比较 `%Y-%m-%d`）；无 `online_date` 的条目保留（由去重兜底）。
- 节流：`delay_seconds`（默认 1.5s），尊重公共服务的低频要求。

### `fetch_napstic_journals`

- 期刊列表：`source.journals`（slug 数组），缺省继承 `cn_napstic.JOURNALS` 全部 11 本。
- 抓取窗口 `source.months`（默认 3），`fetch_details=source.fetch_details`（默认 false，列表页已含中文摘要；true 时逐篇补英文摘要/DOI/卷期）。
- 该通道本身按"最近 N 期"滚动，不按 `since` 精确过滤（由状态文件去重兜底）。
- 每刊失败不中断整体（复用插件 `fetch_recent` 的异常包容）。

## 配置模板变更

`assets/power_system_radar_config.json`、`..._full.json`、`power_system_radar_config.yaml` 三份模板的 `sources` 各新增：

```json
{
  "name": "中文检索(NAPSTIC)",
  "type": "napstic_search",
  "enabled": true,
  "max_results": 40,
  "pages": 2,
  "size": 20,
  "delay_seconds": 1.5
},
{
  "name": "中文核心期刊目录(NAPSTIC)",
  "type": "napstic_journals",
  "enabled": false,
  "max_results": 30,
  "months": 3,
  "fetch_details": false,
  "delay_seconds": 1.5,
  "journals": ["zgdjgcxb","dlxtzdh","dwjs","gdyjs","dgjsxb","jdq","dlzdhsb","zgdl","dljs","hbdldxxb","xddl"]
}
```

三份模板统一：`napstic_search` 默认开启；`napstic_journals` 默认**关闭**（避免默认对 11 刊 × 3 期发起大量低频抓取请求，用户按需在 JSON 中开启）。

## 展示与推送

- 中文条目进入“期刊论文”推送计划，走现有 digest_*.md / digest_*.html / dashboard_*.html / records_*.json 全链路。
- `render_digest_markdown` 在标题下新增一行可选英文标题（若有），保持阅读层中文优先。
- DeepSeek 解读（中文输出）无需改动。

## UI 与文档

- 控制台 UI 无需改主干：新源以通用行展示（开关/候选量），源名用中文便于识别。
- README 新增「中文文献源（NAPSTIC）」章节：来源合规边界、低频节流（默认 1.2–1.5s）、`search` 与 `journals` 的各自用途/差异性（search 追网络首发、journals 追完整目录但滞后 0.5–1.5 年）、英文姊妹刊提示、Crossref 对中文 DOI 不可用的坑。
- `cn_napstic.py` 连同插件三份文档（README/万方补充源说明/验证记录）一并并入仓库：脚本放 `scripts/cn_napstic.py`，文档放 `skills/power-system-literature-radar/references/napstic/` 目录。

## 合规

- 仅个人科研低频使用；默认节流 ≥1.2s；不抓全文、不对外转发。
- NAPSTIC 为期刊方自愿公开的题录摘要，非付费墙绕过；文档中写明遵守平台访问频率。

## 错误处理

- 网络失败/超时/解析失败：抓取器内捕获并 `print("[warn] ...", file=sys.stderr)`，不中断其他源（与现有源一致）。
- `source_fetch_limit` 增加 napstic 上限（`napstic_search: 200`），超限安全截断。
- 单源失败不影响整体 run。

## 测试与验收

单测（mock 网络，`test_power_system_radar.py` 增加用例）：
1. `build_default_query` 中文类型分支 → chinese 组 OR 组合，`query_override` 覆盖；
2. NAPSTIC 记录 → `clean_item` 映射（含 `abstract_en`/`source_id`）；
3. `item_key` 回退顺序（doi → source_id → title）；
4. `dedupe_and_score` 对 `napstic` 前缀源的 min_score 豁免。

实机验证（本机网络）：
1. `--validate-config`、`--dry-run` 通过；
2. `--run` 一次：`napstic_search` 拉到真实中文文献，出现在 records_*.json 与 digest；
3. 单独跑 `napstic_journals` 一次（临时模板）验证逐刊抓取；
4. SMTP 实测：收到 QQ 授权码后配置 `radar.env.ps1`（发件 sender@qq.example.com → 收件 target@zhou.example.com），执行一次真实发送，确认邮件正文+HTML 附件到达。

## 交付

- 实现完成后提交并 push 到 `hfk369258/power-system-academic-radar`（master）。
- 需用户提供：QQ 邮箱 SMTP 授权码（用于步骤 4）。

## 风险

- NAPSTIC 页面结构/接口可能改版 → 解析失败时告警不中断；插件已内置重试。
- search 接口（opaj 域名）偶发 502/超时 → 已内置重试与节流。
- 中文期刊数据滞后（期刊页 0.5–1.5 年）→ 文档注明优先 search 通道追前沿。