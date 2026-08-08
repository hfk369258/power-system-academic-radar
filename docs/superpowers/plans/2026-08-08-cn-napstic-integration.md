# NAPSTIC 中文文献源整合实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本机 cn-abstracts-tool 插件的 NAPSTIC 中文文献抓取能力整合为 power-system-academic-radar 的原生数据源（`napstic_search` + `napstic_journals`），与现有英文源共用抓取→筛选→去重→解读→邮件全链路，并完成 SMTP 真实发送验证与 GitHub 推送。

**Architecture:** `scripts/cn_napstic.py`（插件原样引入，内部自带 HTTP 节流/重试）作为依赖模块被 `scripts/power_system_radar.py` import；新增两个 `fetch_*` 函数挂进 `fetch_enabled_sources` 分发器，经共享转换函数 `napstic_to_item` + `clean_item` 统一 schema；去重键加 `source_id` 回退；`dedupe_and_score` 的 min_score 豁免扩展 `napstic` 前缀；`journal_filter` 白名单补全 11 本中文刊；模板三份（json/json.full/yaml）新增源条目；digest 显示英文题名。

**Tech Stack:** Python 3.10+（仅标准库；YAML 需 PyYAML）、unittest、PowerShell 5.1、Git。

**前置信息（凭据，只进本机 `radar.env.ps1`，已被 `.gitignore` 排除）：**
- SMTP：`smtp.qq.com:465` SSL，账号 `sender@qq.example.com`，授权码 `REPLACE_WITH_SMTP_AUTH_CODE`
- 收件：`target@zhou.example.com`
- 工作副本：`C:\Users\msi\ZCodeProject\radar-repo`（分支 `main`，origin 指向 `hfk369258/power-system-academic-radar`）

**设计文档：** `docs/superpowers/specs/2026-08-08-cn-napstic-integration-design.md`

---

### Task 0: 环境与基线确认

- [ ] **Step 1: 确认 Python 可用**

Run: `python --version`
Expected: `Python 3.10.x` 或更高（若报错，改用 `py -3`，后续所有命令同步替换）。

- [ ] **Step 2: 确认基线测试全绿**

Run: `cd /c/Users/msi/ZCodeProject/radar-repo && python scripts/test_power_system_radar.py -v`
Expected: `OK`，`Ran 16 tests`。

- [ ] **Step 3: 确认工作树干净**

Run: `git status --short`
Expected: 空输出（当前 HEAD 为 `d41dae6`，含设计文档提交）。

---

### Task 1: 引入插件模块与文档

**Files:**
- Copy: `F:\data\data.zhuomian\claude\cn-abstracts-tool\cn_napstic.py` → `scripts/cn_napstic.py`
- Copy: `F:\data\data.zhuomian\claude\cn-abstracts-tool\README.md` → `skills/power-system-literature-radar/references/napstic/README.md`
- Copy: `F:\data\data.zhuomian\claude\cn-abstracts-tool\万方补充源说明.md` → `skills/power-system-literature-radar/references/napstic/万方补充源说明.md`
- Copy: `F:\data\data.zhuomian\claude\cn-abstracts-tool\验证记录.md` → `skills/power-system-literature-radar/references/napstic/验证记录.md`

- [ ] **Step 1: 复制脚本与三份文档**（`cp` 即可，目标目录不存在则 `mkdir -p`）

- [ ] **Step 2: 验证模块可独立导入**

Run:
```bash
cd /c/Users/msi/ZCodeProject/radar-repo
python -c "import sys; sys.path.insert(0, 'scripts'); import cn_napstic; print(len(cn_napstic.JOURNALS)); print(cn_napstic.SEARCH_URL)"
```
Expected:
```
11
https://opaj.napstic.cn/search/literature
```

- [ ] **Step 3: 提交**

```bash
git add scripts/cn_napstic.py skills/power-system-literature-radar/references/napstic/
git commit -m "feat: vendor cn_napstic.py and its reference docs for CN-literature source"
```

---

### Task 2: clean_item 扩展中文字段 + item_key 增加 source_id 回退（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py:200-219`（clean_item）、`:153-158`（item_key）
- Test: `scripts/test_power_system_radar.py`（新增 2 个用例）

- [ ] **Step 1: 写失败测试**（先给测试文件顶部 import 区加一行 `import datetime as dt`，再在类 `RadarStateTests` 内追加用例）

```python
    def test_clean_item_keeps_chinese_literature_fields(self) -> None:
        raw = {
            "title_cn": "构网型储能系统研究",
            "title_en": "Grid-Forming Energy Storage Research",
            "abstract_cn": "中文摘要。",
            "abstract_en": "English abstract.",
            "authors_cn": ["张三"],
            "journal_cn": "电网技术",
            "doi": "10.13335/j.1000-3673.pst.2024.0001",
            "detail_url": "https://search.napstic.cn/literature/periodical/010dwjs202401001",
            "keywords_cn": ["构网型", "储能"],
            "source_id": "dwjs202401001",
            "year": "2024",
            "online_date": "2025-05-08 02:01:04",
        }
        item = radar.clean_item(radar.napstic_to_item(raw, {"name": "中文检索(NAPSTIC)"}))

        self.assertEqual(item["title"], "构网型储能系统研究")
        self.assertEqual(item["title_en"], "Grid-Forming Energy Storage Research")
        self.assertEqual(item["abstract"], "中文摘要。")
        self.assertEqual(item["abstract_zh"], "中文摘要。")
        self.assertEqual(item["abstract_en"], "English abstract.")
        self.assertEqual(item["venue"], "电网技术")
        self.assertEqual(item["doi"], "10.13335/j.1000-3673.pst.2024.0001")
        self.assertEqual(item["url"], "https://search.napstic.cn/literature/periodical/010dwjs202401001")
        self.assertEqual(item["source_id"], "dwjs202401001")
        self.assertEqual(item["published"], "2025-05-08 02:01:04")
        self.assertEqual(item["source"], "中文检索(NAPSTIC)")

    def test_item_key_falls_back_to_source_id(self) -> None:
        with_doi = {"doi": "10.1/x", "source_id": "abc", "title": "t"}
        no_doi = {"source_id": "dwjs202401001", "title": "无 DOI 论文"}
        no_id = {"title": "既无 DOI 也无 ID"}

        self.assertTrue(radar.item_key(with_doi).startswith("doi:"))
        self.assertTrue(radar.item_key(no_doi).startswith("sid:"))
        self.assertTrue(radar.item_key(no_id).startswith("title:"))
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_clean_item|test_item_key|FAIL|ERROR"`
Expected: 两个用例 `ERROR`（`AttributeError: module ... has no attribute 'napstic_to_item'`）——测试先于实现，符合 TDD。

- [ ] **Step 3: 实现 `napstic_to_item` 与 `clean_item` 扩展**（在 `clean_item` 定义之前插入 `napstic_to_item`；`clean_item` 增加三行）

在 `scripts/power_system_radar.py` 中，`clean_item`（`:200`）之前插入：

```python
def napstic_to_item(rec: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """把 cn_napstic 记录映射为雷达统一字段（title_cn 为主，英文保留）。"""
    return {
        "title": rec.get("title_cn") or rec.get("title_en") or "",
        "title_en": rec.get("title_en") or "",
        "abstract": rec.get("abstract_cn") or "",
        "abstract_zh": rec.get("abstract_cn") or "",
        "abstract_en": rec.get("abstract_en") or "",
        "authors": rec.get("authors_cn") or [],
        "venue": rec.get("journal_cn") or rec.get("journal_en") or "",
        "doi": rec.get("doi") or "",
        "url": rec.get("detail_url") or "",
        "keywords": rec.get("keywords_cn") or [],
        "source_id": rec.get("source_id") or "",
        "year": rec.get("year") or "",
        "published": rec.get("online_date") or rec.get("year") or "",
        "source": source["name"],
        "origin": rec.get("detail_url") or rec.get("source_id") or "",
        "publication_type": "journal",
        "venue_type": "journal",
    }
```

`clean_item` 的 return 字典（`:200-219`）末尾、`"source"` 行之前增加三行：

```python
        "title_en": normalize_space(item.get("title_en")),
        "abstract_en": normalize_space(item.get("abstract_en")),
        "source_id": normalize_space(item.get("source_id")),
```

`item_key`（`:153-158`）替换为：

```python
def item_key(item: dict[str, Any]) -> str:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return f"doi:{doi}"
    source_id = normalize_space(item.get("source_id"))
    if source_id:
        return "sid:" + hashlib.sha1(source_id.encode("utf-8")).hexdigest()
    title = normalize_space(item.get("title")).lower()
    return "title:" + hashlib.sha1(title.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 运行确认通过**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_clean_item|test_item_key|OK|FAILED"`
Expected: 两个用例 `ok`，整体 `OK`。

- [ ] **Step 5: 提交**

```bash
git add scripts/power_system_radar.py scripts/test_power_system_radar.py
git commit -m "feat: map NAPSTIC records to unified schema and add source_id dedupe key"
```

---

### Task 3: 中文查询分支覆盖 napstic_search（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py:222-259`（build_default_query）
- Test: `scripts/test_power_system_radar.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_build_default_query_uses_chinese_keywords_for_napstic(self) -> None:
        config = {
            "keywords": {
                "core": ["power system"],
                "chinese": ["电力系统", "构网型"],
                "exclude": [],
            },
            "queries": {"auto_from_keywords": True, "chinese": "legacy"},
        }
        query = radar.build_default_query(config, "napstic_search")

        self.assertIn("电力系统", query)
        self.assertIn("构网型", query)
        self.assertNotIn("power system", query)
        override = radar.source_query(
            config, {"type": "napstic_search", "query_override": "构网型 AND 储能"}
        )
        self.assertEqual(override, "构网型 AND 储能")
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_build_default_query_uses_chinese|FAIL|ERROR"`
Expected: `FAIL`（napstic_search 走英文分支，断言 `电力系统 in query` 不成立）。

- [ ] **Step 3: 实现**（`:222` 的 `def build_default_query` 之前加常量，替换两处判断）

在 `build_default_query` 之前插入：

```python
CHINESE_QUERY_TYPES = {"cnki", "manual_chinese", "napstic_search"}
```

两处 `if source_type in {"cnki", "manual_chinese"}:`（`:240` 与 `:257`）均替换为：

```python
        if source_type in CHINESE_QUERY_TYPES:
```

- [ ] **Step 4: 运行确认通过**（命令同 Step 2）
Expected: 新增用例 `ok`，整体 `OK`。

- [ ] **Step 5: 提交**

```bash
git add scripts/power_system_radar.py scripts/test_power_system_radar.py
git commit -m "feat: route napstic_search to the Chinese keyword query builder"
```

---

### Task 4: fetch_napstic_search 抓取器（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py`（顶部 import 区 + `fetch_*` 区域，建议放在 `fetch_rss`（`:673`）之后）
- Test: `scripts/test_power_system_radar.py`

- [ ] **Step 1: 加防护性 import**（`power_system_radar.py` 顶部，`USER_AGENT` 常量定义之前）

```python
# cn_napstic 是本仓库自带模块（scripts/cn_napstic.py）；缺失时对应源只告警跳过，不影响其他源。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cn_napstic  # noqa: E402
except ImportError:  # pragma: no cover
    cn_napstic = None
```

- [ ] **Step 2: 写失败测试**

```python
    def test_napstic_search_maps_records_and_filters_by_online_date(self) -> None:
        fresh = {
            "source": "napstic-search",
            "source_id": "0120250800425188",
            "title_cn": "构网型变流器控制",
            "abstract_cn": "新摘要",
            "journal_cn": "电网技术",
            "year": "2025",
            "online_date": "2026-08-01 10:00:00",
            "doi": "10.13335/x.1",
            "detail_url": "https://search.napstic.cn/literature/periodical/010x202501001",
        }
        stale = dict(fresh, title_cn="旧论文", source_id="stale1", online_date="2026-05-01 10:00:00")
        config = {"keywords": {"chinese": ["构网型"]}, "queries": {"auto_from_keywords": True}}
        source = {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "size": 20, "pages": 2, "delay_seconds": 0}

        with mock.patch.object(radar.cn_napstic, "search_literature", return_value=([fresh, stale], 99)) as call_mock:
            items = radar.fetch_napstic_search(config, source, dt.date(2026, 7, 1), 40)

        self.assertEqual(call_mock.call_count, 2)
        self.assertEqual(call_mock.call_args.args[0], "构网型")
        self.assertEqual([item["title"] for item in items], ["构网型变流器控制"])
        self.assertEqual(items[0]["source"], "中文检索(NAPSTIC)")

    def test_napstic_search_degrades_when_module_missing(self) -> None:
        with mock.patch.object(radar, "cn_napstic", None):
            items = radar.fetch_napstic_search(
                {"keywords": {}}, {"name": "x", "type": "napstic_search"}, dt.date(2026, 7, 1), 40
            )
        self.assertEqual(items, [])
```

- [ ] **Step 3: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_napstic_search|FAIL|ERROR"`
Expected: `ERROR`（`AttributeError: module ... has no attribute 'fetch_napstic_search'`）。

- [ ] **Step 4: 实现**（放在 `fetch_rss` 函数之后；**实测修正**：opaj 检索接口把空格分词按 AND 处理、不支持 OR 语法——OR 拼成的复合查询会返回 0。因此改为 `napstic_query_terms()` 把 chinese 组逐词拆开独立查询，词组内按 DOI/source_id 去重；`query_override` 或 auto_from_keywords 关闭时原样单发）

```python
def napstic_query_terms(config: dict[str, Any], source: dict[str, Any]) -> list[str]:
    """NAPSTIC 检索接口把空格分隔的词按 AND 处理，不支持 OR/AND 语法（实测 OR 词被忽略、多词合并后可能命中 0）。

    auto_from_keywords 模式下把 `chinese` 组的每个关键词拆成独立查询；配置了 query_override 时原样作为单个查询。
    """
    query = source_query(config, source)
    if not query:
        return []
    if (config.get("queries") or {}).get("auto_from_keywords") and not source.get("query_override"):
        terms: list[str] = []
        for raw in (config.get("keywords") or {}).get("chinese", []):
            term = normalize_space(raw)
            if term and term not in terms:
                terms.append(term)
        return terms
    return [query]


def fetch_napstic_search(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    """按 chinese 关键词组逐词检索 NAPSTIC（含网络首发），按 online_date 增量过滤。"""
    if cn_napstic is None:
        print(f"[warn] {source['name']} skipped: cn_napstic module not found", file=sys.stderr)
        return []
    terms = napstic_query_terms(config, source)
    if not terms:
        print(f"[warn] {source['name']} skipped: empty query", file=sys.stderr)
        return []
    size = max(1, int(source.get("size", 20)))
    delay = float(source.get("delay_seconds", 1.5))
    since_str = since.strftime("%Y-%m-%d") if since else ""
    records: list[dict[str, Any]] = []
    total = 0
    for term in terms:
        try:
            page_records, term_total = cn_napstic.search_literature(term, size=size, page=1, delay=delay)
            total += int(term_total)
            records.extend(page_records)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {source['name']} term '{term}' failed: {exc}", file=sys.stderr)
        time.sleep(delay * 0.5)
    # 同一关键词族下多词可能命中同一篇：按 DOI/source_id 先去重
    seen_keys: set[str] = set()
    items = []
    bypass = bool(source.get("bypass_journal_whitelist", False))
    for rec in records:
        doi = normalize_doi(rec.get("doi"))
        rec_key = doi or normalize_space(rec.get("source_id"))
        if rec_key:
            if rec_key in seen_keys:
                continue
            seen_keys.add(rec_key)
        if since_str and rec.get("online_date") and rec["online_date"][:10] < since_str:
            continue
        item = clean_item(napstic_to_item(rec, source))
        if bypass:
            item["bypass_journal_whitelist"] = True
        items.append(item)
    print(f"[info] {source['name']} terms={len(terms)}: platform hit {total}, fetched {len(items)}", file=sys.stderr)
    return items[:limit]
```

配套测试（替换原两个测试）：
- 单关键词：`search_literature` 调用 1 次、参数为该词；`online_date < since` 记录被过滤；
- 多关键词：每个词各调用 1 次，相同 `source_id` 记录只保留 1 条；
- `napstic_query_terms`：auto_from_keywords 关闭 + query_override 时返回 `[override]`；
- `journal_filter_match` 尊重 `bypass_journal_whitelist` 标记（源配置开启时跳过白名单——实测检索通道覆盖全站上万种期刊，白名单会让 search 恒为 0 篇）。

---

### Task 5: fetch_napstic_journals 抓取器（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py`（`fetch_napstic_search` 之后）
- Test: `scripts/test_power_system_radar.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_napstic_journals_fetches_configured_slugs(self) -> None:
        article = {
            "source": "napstic",
            "source_id": "zgdjgcxb202417002",
            "title_cn": "双碳目标下能源电力系统趋势",
            "title_en": "Energy transition trends",
            "abstract_cn": "摘要",
            "journal_cn": "中国电机工程学报",
            "year": "2024",
            "issue": "17",
            "pages": "6707-6720",
            "doi": "10.13334/j.0258-8013.pcsee.240634",
            "detail_url": "https://search.napstic.cn/literature/periodical/010zgdjgcxb202417002",
        }
        source = {
            "name": "中文核心期刊目录(NAPSTIC)",
            "type": "napstic_journals",
            "months": 3,
            "delay_seconds": 0,
            "journals": ["zgdjgcxb", "bad-slug"],
        }

        with mock.patch.object(radar.cn_napstic, "fetch_recent", return_value=([article], [(2024, 17, 1)])) as call_mock:
            items = radar.fetch_napstic_journals({}, source, dt.date(2026, 7, 1), 30)

        self.assertEqual(call_mock.call_count, 1)  # bad-slug 被跳过
        self.assertEqual(call_mock.call_args.args[0], "zgdjgcxb")
        self.assertEqual(items[0]["title"], "双碳目标下能源电力系统趋势")
        self.assertEqual(items[0]["title_en"], "Energy transition trends")
        self.assertEqual(items[0]["venue"], "中国电机工程学报")
        self.assertEqual(items[0]["source"], "中文核心期刊目录(NAPSTIC)")
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_napstic_journals|FAIL|ERROR"`
Expected: `ERROR`（无 `fetch_napstic_journals`）。

- [ ] **Step 3: 实现**

```python
def fetch_napstic_journals(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    """按期刊 slug 抓最近 N 期（滚动窗口，去重由状态文件兜底）。"""
    if cn_napstic is None:
        print(f"[warn] {source['name']} skipped: cn_napstic module not found", file=sys.stderr)
        return []
    delay = float(source.get("delay_seconds", 1.5))
    months = max(1, int(source.get("months", 3)))
    fetch_details = bool(source.get("fetch_details", False))
    slugs = list(source.get("journals") or cn_napstic.JOURNALS)
    items: list[dict[str, Any]] = []
    for slug in slugs:
        if slug not in cn_napstic.JOURNALS:
            print(f"[warn] {source['name']}: unknown journal slug skipped: {slug}", file=sys.stderr)
            continue
        try:
            articles, _ = cn_napstic.fetch_recent(slug, months=months, fetch_details=fetch_details, delay=delay)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {source['name']}: journal {slug} failed: {exc}", file=sys.stderr)
            continue
        for article in articles:
            items.append(clean_item(napstic_to_item(article, source)))
        if len(items) >= limit:
            break
        time.sleep(delay)
    return items[:limit]
```

- [ ] **Step 4: 运行确认通过**（命令同 Step 2）
Expected: `ok`，整体 `OK`。

- [ ] **Step 5: 提交**

```bash
git add scripts/power_system_radar.py scripts/test_power_system_radar.py
git commit -m "feat: add fetch_napstic_journals source for per-journal recent issues"
```

---

### Task 6: 分发器、容量上限与评分豁免（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py:2264-2305`（source_fetch_limit、fetch_enabled_sources）、`:1251`（min_score 豁免）
- Test: `scripts/test_power_system_radar.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_napstic_bypasses_min_score_like_manual_exports(self) -> None:
        config = {"scoring": {"min_score": 5}, "journal_filter": {"enabled": False}}
        item = {"title": "低相关度中文论文", "source": "中文检索(NAPSTIC)", "hits": ["chinese:储能"]}

        result = radar.dedupe_and_score([item], config, set())

        self.assertEqual(len(result), 1)

    def test_fetch_enabled_sources_dispatches_napstic_types(self) -> None:
        config = {
            "sources": [
                {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "enabled": True},
                {"name": "中文核心期刊目录(NAPSTIC)", "type": "napstic_journals", "enabled": True},
            ]
        }

        with mock.patch.object(radar, "fetch_napstic_search", return_value=[{"title": "a"}]), mock.patch.object(
            radar, "fetch_napstic_journals", return_value=[{"title": "b"}]
        ), mock.patch.object(radar, "load_manual_exports", return_value=[]):
            result = radar.fetch_enabled_sources(config, radar.utc_today(), Path.cwd(), 20)

        self.assertEqual(sorted(item["title"] for item in result), ["a", "b"])

    def test_napstic_search_candidate_limit_is_capped(self) -> None:
        self.assertEqual(
            radar.source_fetch_limit({"profile": {"max_results_per_source": 25}}, {"type": "napstic_search"}, 500),
            200,
        )
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_napstic_bypasses|test_fetch_enabled_sources_dispatches|test_napstic_search_candidate|FAIL|ERROR"`
Expected: `FAIL`/`ERROR`（新类型未分发、未豁免、无上限）。

- [ ] **Step 3: 实现三处修改**

a) `source_fetch_limit` 的 caps 字典（`:2267-2273`）增加一行：

```python
        "napstic_search": 200,
```

b) `fetch_enabled_sources` 的 elif 链（`:2298` 的 `elif source_type == "rss":` 之后）增加：

```python
            elif source_type == "napstic_search":
                items.extend(fetch_napstic_search(config, source, since, limit))
            elif source_type == "napstic_journals":
                items.extend(fetch_napstic_journals(config, source, since, limit))
```

c) `dedupe_and_score` 的豁免条件（`:1251`）替换为：

```python
        src = str(item.get("source", ""))
        if score < min_score and not (src.startswith(("manual", "napstic")) or "napstic" in src.lower()):
            continue
```

（注意：`clean_item` 中 `source` 保存的是配置里的源名，如 `中文检索(NAPSTIC)`，必须用大小写不敏感的包含判断。）

- [ ] **Step 4: 运行确认通过**（命令同 Step 2）
Expected: 三个用例 `ok`，整体 `OK`。

- [ ] **Step 5: 提交**

```bash
git add scripts/power_system_radar.py scripts/test_power_system_radar.py
git commit -m "feat: dispatch napstic sources, cap search limit, exempt napstic from min_score"
```

---

### Task 7: 三份配置模板新增中文源 + journal_filter 补全 11 刊

**Files:**
- Modify: `assets/power_system_radar_config.json`
- Modify: `assets/power_system_radar_config_full.json`
- Modify: `assets/power_system_radar_config.yaml`

- [ ] **Step 1: JSON 模板（basic + full）sources 数组末尾追加两个源**

用 Edit 工具，锚定 `journal_rss` 源条目结束（`"urls": [...]` 块之后 `},` 与 sources 数组的 `],` 之间），插入：

```json
  {
   "name": "中文检索(NAPSTIC)",
   "type": "napstic_search",
   "enabled": true,
   "max_results": 40,
   "size": 20,
   "delay_seconds": 1.5,
   "bypass_journal_whitelist": true
  },
  {
   "name": "中文核心期刊目录(NAPSTIC)",
   "type": "napstic_journals",
   "enabled": false,
   "max_results": 30,
   "months": 3,
   "fetch_details": false,
   "delay_seconds": 1.5,
   "journals": ["zgdjgcxb", "dlxtzdh", "dwjs", "gdyjs", "dgjsxb", "jdq", "dlzdhsb", "zgdl", "dljs", "hbdldxxb", "xddl"]
  }
```

（注意：`napstic_search` 需 `bypass_journal_whitelist: true`——检索通道覆盖全站上万种期刊，绝大多数不在 `chinese_ei` 白名单，不加此开关 search 恒为 0 篇。`pages` 字段已移除，改为每词一页 `size` 条。）

（先 Read 对应文件确认锚点处实际缩进，保持一致。）

- [ ] **Step 2: YAML 模板追加**

锚定 `sources:` 数组末尾（`journal_rss` 条目后），插入（缩进与文件中一致）：

```yaml
  - name: 中文检索(NAPSTIC)
    type: napstic_search
    enabled: true
    max_results: 40
    pages: 2
    size: 20
    delay_seconds: 1.5
  - name: 中文核心期刊目录(NAPSTIC)
    type: napstic_journals
    enabled: false
    max_results: 30
    months: 3
    fetch_details: false
    delay_seconds: 1.5
    journals:
      - zgdjgcxb
      - dlxtzdh
      - dwjs
      - gdyjs
      - dgjsxb
      - jdq
      - dlzdhsb
      - zgdl
      - dljs
      - hbdldxxb
      - xddl
```

- [ ] **Step 3: journal_filter.chinese_ei 补全 4 本刊（三份模板）**

在三个模板的 `journal_filter.categories.chinese_ei` 列表末尾追加：

```
中国电力
电力建设
华北电力大学学报
现代电力
```

（JSON 与 YAML 语法分别对应。`journal_filter_match` 是子串匹配，`华北电力大学学报(自然科学版)` 可命中 `华北电力大学学报`。）

- [ ] **Step 4: 三份模板全部通过校验**

Run:
```bash
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --validate-config
python scripts/power_system_radar.py --config assets/power_system_radar_config_full.json --validate-config
python scripts/power_system_radar.py --config assets/power_system_radar_config.yaml --validate-config
```
Expected: 每次输出含 `Config OK`，且 `enabled sources:` 列表含 `中文检索(NAPSTIC)`。若 YAML 报缺 PyYAML：`python -m pip install pyyaml` 后重跑。

- [ ] **Step 5: dry-run 确认中文检索式**

Run:
```bash
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --dry-run
```
Expected: `- 中文检索(NAPSTIC) [napstic_search, enabled]: 电力系统 OR 智能电网 OR 配电网 OR ...`（chinese 组 OR 组合）。

- [ ] **Step 6: 提交**

```bash
git add assets/
git commit -m "feat: add NAPSTIC CN-literature sources and complete chinese_ei whitelist in templates"
```

---

### Task 8: digest 增加英文题名行（TDD）

**Files:**
- Modify: `scripts/power_system_radar.py:1705-1718`（render_digest_markdown 基本信息区）
- Test: `scripts/test_power_system_radar.py`

- [ ] **Step 1: 写失败测试**

```python
    def test_digest_markdown_shows_english_title_when_present(self) -> None:
        item = {
            "title": "构网型储能系统研究",
            "title_en": "Grid-Forming Energy Storage Research",
            "venue": "电网技术",
            "year": "2025",
            "authors": ["张三"],
            "doi": "",
            "url": "",
            "oa_url": "",
            "journal_filter_hits": ["电网技术"],
            "hits": ["chinese:构网型"],
            "score": 6,
            "publication_type": "journal",
            "abstract": "摘要",
            "is_oa": False,
        }
        md = radar.render_digest_markdown([item], {"profile": {"name": "test"}})

        self.assertIn("英文题名", md)
        self.assertIn("Grid-Forming Energy Storage Research", md)
```

- [ ] **Step 2: 运行确认失败**

Run: `python scripts/test_power_system_radar.py -v 2>&1 | grep -E "test_digest_markdown_shows_english|FAIL"`
Expected: `FAIL`（`AssertionError: '英文题名' not found in ...`）。

- [ ] **Step 3: 实现**（`render_digest_markdown` 中，基本信息 `lines.extend([...])` 之后、"### 2. 筛选判断" 之前插入）

```python
        if item.get("title_en"):
            lines.extend([f"- 英文题名：{item.get('title_en')}", ""])
```

- [ ] **Step 4: 运行确认通过**（命令同 Step 2）
Expected: `ok`，整体 `OK`。

- [ ] **Step 5: 提交**

```bash
git add scripts/power_system_radar.py scripts/test_power_system_radar.py
git commit -m "feat: show English title in markdown digest for CN-literature items"
```

---

### Task 9: README 与 SKILL 文档更新

**Files:**
- Modify: `README.md`（新增「中文文献源（NAPSTIC）」章节，位置在「API 与数据源配置」的「无需 API Key 的来源」之后）
- Modify: `skills/power-system-literature-radar/SKILL.md`（在 Source 相关段落补一句 NAPSTIC 指引）

- [ ] **Step 1: README 增加章节**（内容要点）

```markdown
### NAPSTIC 中文文献源（CNKI 系核心期刊）

- 数据来自「国家学术搜索」(search.napstic.cn)，由中信所(ISTIC)运营，期刊方自愿公开题录/摘要/DOI，非付费墙绕过；仅限个人科研低频使用，默认节流 1.5s。
- 两种源类型：
  - `napstic_search`（默认开启）：按 `keywords.chinese` 关键词组检索全部中文期刊，含网络首发（`online_date`）增量，数据最新；
  - `napstic_journals`（默认关闭）：按 `journals` slug 列表逐刊抓最近 `months` 期，覆盖完整但滞后约 0.5~1.5 年；开启前请确认能接受其请求量。
- 中文文献同样受 `journal_filter` 白名单约束，模板已内置 `chinese_ei` 11 本刊（可在控制台/JSON 中增删）。
- 已知坑：中国电力期刊 DOI 注册在 ISTIC 中国DOI系统，**不在 Crossref**（Crossref 拿不到中文刊数据）；英文姊妹刊（CSEE JPES / PCMP / MPCE）为独立英文刊，仅覆盖中文刊约 5~10% 精华，完整跟踪中文论文请用 NAPSTIC。
- 期刊 slug 表与合规说明见 `skills/power-system-literature-radar/references/napstic/`。
```

- [ ] **Step 2: SKILL.md 补一句**（在「## Journal and OA Filtering」段前加）

```markdown
## 中文文献源（NAPSTIC）

雷达内置 `napstic_search`（关键词检索，含网络首发）与 `napstic_journals`（11 本核心期刊逐期抓取）两个中文数据源，无需 API Key。抓取脚本为 `scripts/cn_napstic.py`，默认节流 ≥1.2s。中文文献与英文文献共用筛选、去重与邮件链路；受 `journal_filter.chinese_ei` 白名单约束。详细说明与合规边界见 `references/napstic/`。
```

- [ ] **Step 3: 提交**

```bash
git add README.md skills/
git commit -m "docs: document NAPSTIC CN-literature sources and compliance boundary"
```

---

### Task 10: 实机网络验证（不改仓库内容）

- [ ] **Step 1: 真实检索一次（临时状态文件，避免污染工作区）**

Run:
```bash
cd /c/Users/msi/ZCodeProject/radar-repo
python scripts/power_system_radar.py --config assets/power_system_radar_config.json --run --no-state --max-results 25
```
Expected: stderr 出现 `[info] 中文检索(NAPSTIC) '...': platform hit N, fetched M`（M>0），`records: M`，digest/html/dashboard/json 四个输出路径打印且文件存在。用以下命令抽查记录：

Run: `python -c "import json,glob; fs=glob.glob('outputs/**/records_*.json',recursive=True); d=json.load(open(fs[-1],encoding='utf-8')); print(len(d)); print([r.get('source') for r in d[:5]]); print([r.get('title','')[:20] for r in d[:3]])"`
Expected: 存在 `source` 为 `中文检索(NAPSTIC)` 的记录，标题为中文。

（若 stderr 出现 `[warn] state not updated because no enabled notification completed successfully` 属正常——本步骤故意不配 SMTP 环境变量；也可能出现 email 未配置的 warn，均不影响产物生成。）

- [ ] **Step 2: 期刊通道冒烟（临时模板：1 个月、2 刊，避免大请求量）**

先创建 `work/` 下临时配置（复制 basic 配置后修改，`work/` 已 gitignore）：

Run:
```bash
python - <<'EOF'
import json, shutil, pathlib
src = pathlib.Path('assets/power_system_radar_config.json')
cfg = json.loads(src.read_text(encoding='utf-8'))
for s in cfg['sources']:
    if s['type'] == 'napstic_search':
        s['enabled'] = False
    if s['type'] == 'napstic_journals':
        s['enabled'] = True
        s['months'] = 1
        s['journals'] = ['zgdjgcxb', 'dlxtzdh']
        s['delay_seconds'] = 1.5
cfg['profile']['state_file'] = 'work/radar-smoke/state.json'
cfg['profile']['output_dir'] = 'outputs/radar-smoke'
pathlib.Path('work/radar-smoke').mkdir(parents=True, exist_ok=True)
pathlib.Path('work/radar-smoke/config.json').write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
print('written work/radar-smoke/config.json')
EOF
python scripts/power_system_radar.py --config work/radar-smoke/config.json --run --no-state --max-results 30
```
Expected: stderr 显示 `中文核心期刊目录(NAPSTIC)` 抓取日志且 `records: N`（N>0），输出目录 `outputs/radar-smoke/` 有产物；抽查 JSON 中 `source == 中文核心期刊目录(NAPSTIC)` 的记录存在。

- [ ] **Step 3: 冒烟产物不提交**（`outputs/`、`work/` 已在 `.gitignore`）

Run: `git status --short`
Expected: 无新未跟踪文件出现（除已提交改动）。

---

### Task 11: SMTP 真实邮件验证

**Files:**
- Create: `radar.env.ps1`（本机，gitignored，**严禁提交**）

- [ ] **Step 1: 创建 radar.env.ps1**（⚠️ 实测教训：PowerShell 5.1 按 ANSI 解析无 BOM 的 .ps1，**文件内不得包含中文注释**，否则部分行可能解析异常；示例 `radar.env.example.ps1` 已是纯 ASCII）

```powershell
$env:RADAR_SMTP_HOST = "smtp.qq.com"
$env:RADAR_SMTP_PORT = "465"
$env:RADAR_SMTP_USER = "sender@qq.example.com"
$env:RADAR_SMTP_PASSWORD = "REPLACE_WITH_SMTP_AUTH_CODE"
$env:RADAR_EMAIL_FROM = "sender@qq.example.com"
$env:RADAR_EMAIL_TO = "target@zhou.example.com"
```

- [ ] **Step 2: 确认被 gitignore**

Run: `git check-ignore -v radar.env.ps1`
Expected: 输出 `.gitignore:1:radar.env.ps1	radar.env.ps1`。

- [ ] **Step 3: 真实发送**

Run（Git Bash 中调 PowerShell）：
```bash
cd /c/Users/msi/ZCodeProject/radar-repo
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_radar.ps1 -ConfigPath assets\power_system_radar_config.json -DocumentType journal -EnableEmail
```
Expected: 日志无 SMTP 报错；运行结束后 `git status --short` 中 `work/power-system-radar/state_basic.json` 为已跟踪或出现但被 ignore（状态文件写入 = 邮件送达成功）；**请用户确认 `target@zhou.example.com` 收到带 HTML 附件的中文简报**。

- [ ] **Step 4: 失败排查预案**（仅当 Step 3 失败时）

1. `RADAR_SMTP_PORT=465` + `use_ssl: true`（模板默认已正确）；若报 SSL 握手失败，检查是否被本机安全软件拦截；
2. 授权码错误会报 `SMTPAuthenticationError` → 与用户核对授权码；
3. 若提示"发信频率限制"，稍后重试。

---

### Task 12: 全量回归 + 提交推送

- [ ] **Step 1: 全量单测**

Run: `python scripts/test_power_system_radar.py -v`
Expected: `OK`（原 16 + 新增 10 = 26 个用例）。

- [ ] **Step 2: 确认无敏感信息进仓库**

Run: `git status --short && git diff --cached --stat`
Expected: 无 `radar.env.ps1`；无 `outputs/`、`work/`、`logs/` 相关文件。

- [ ] **Step 3: 整理提交并推送**

```bash
git add -A
git commit -m "chore: finalize NAPSTIC CN-literature integration (spec + plan + docs)"
git push origin main
```
Expected: push 成功（origin = https://github.com/hfk369258/power-system-academic-radar.git）。推送后执行 `git log --oneline -8` 确认提交链完整。

- [ ] **Step 4: 通知用户**

向用户汇报：推送的分支/commit、中文源开启方式（控制台或 JSON）、SMTP 验证结果，以及后续使用建议（search 每周、journals 按需开启）。
