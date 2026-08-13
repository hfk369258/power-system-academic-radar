#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cn_napstic.py — 从「国家学术搜索」(NAPSTIC, search.napstic.cn) 抓取中文核心期刊的题录 + 摘要。

为什么用 NAPSTIC 而不是知网：
  - 知网无公开 API，且 2024 知网诉秘塔、2025《反不正当竞争法》新增数据条款后，爬取知网摘要题录的法律风险很高。
  - NAPSTIC 是「中国科学技术信息研究所(ISTIC)/国家科技期刊平台」运营的国家级开放平台，
    期刊摘要、DOI、中英文题名/摘要都是其自愿公开提供的数据，低频、个人用途、少量抓取属正常使用。
  - 页面是服务端渲染的静态 HTML，无验证码、无反爬，用普通 HTTP 请求即可稳定获取。

数据字段（detail 页全开时）：
  中文标题 / 英文标题 / 中文摘要 / 英文摘要 / 作者 / 单位 / 中文关键词 / 英文关键词 /
  期刊名(中/英) / 年 / 卷 / 期 / 页码 / DOI / 详情页 URL

用法示例：
  python cn_napstic.py list zgdjgcxb                     # 列出某期刊可用的年份和期数
  python cn_napstic.py fetch zgdjgcxb --year 2024 --issue 17          # 抓某期（列表页，快）
  python cn_napstic.py fetch zgdjgcxb --year 2024 --issue 17 --full   # 抓某期并逐篇补全详情（含英文摘要/DOI）
  python cn_napstic.py recent zgdjgcxb --months 6 --full              # 抓最近 N 个月，适合雷达定时增量
  python cn_napstic.py recent-all --months 3 --full                   # 抓配置里所有期刊最近 N 个月

注意：这是公共服务，请控制频率。脚本默认每次请求间隔 1.2 秒（--delay 可调）。
"""

import argparse
import json
import random
import re
import sys
import threading
import time
import urllib.request
import urllib.parse
from html import unescape

BASE = "https://search.napstic.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# ----------------------------------------------------------------------------
# 期刊配置：slug 是 NAPSTIC 上期刊页的拼音缩写（已实测验证）。需要增删期刊直接改这个表即可。
# ----------------------------------------------------------------------------
JOURNALS = {
    "zgdjgcxb": {"name_cn": "中国电机工程学报", "name_en": "Proceedings of the CSEE", "issn": "0258-8013"},
    "dlxtzdh":  {"name_cn": "电力系统自动化",   "name_en": "Automation of Electric Power Systems", "issn": "1000-1026"},
    "dwjs":     {"name_cn": "电网技术",         "name_en": "Power System Technology", "issn": "1000-3673"},
    "gdyjs":    {"name_cn": "高电压技术",       "name_en": "High Voltage Engineering", "issn": "1003-6520"},
    "dgjsxb":   {"name_cn": "电工技术学报",     "name_en": "Transactions of China Electrotechnical Society", "issn": "1000-6753"},
    "jdq":      {"name_cn": "电力系统保护与控制", "name_en": "Power System Protection and Control", "issn": "1674-3415"},
    "dlzdhsb":  {"name_cn": "电力自动化设备",   "name_en": "Electric Power Automation Equipment", "issn": "1006-6047"},
    "zgdl":     {"name_cn": "中国电力",         "name_en": "Electric Power", "issn": "1004-9649"},
    "dljs":     {"name_cn": "电力建设",         "name_en": "Electric Power Construction", "issn": "1000-7229"},
    "hbdldxxb": {"name_cn": "华北电力大学学报(自然科学版)", "name_en": "Journal of North China Electric Power University", "issn": "1007-2691"},
    "xddl":     {"name_cn": "现代电力",         "name_en": "Modern Electric Power", "issn": "1007-2322"},
}


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
_LAST_REQUEST = 0.0
_THROTTLE_LOCK = threading.Lock()


def http_get(url: str, timeout: int = 60, retries: int = 3, delay: float = 1.2):
    """带 UA、超时、指数退避重试与全局最小间隔节流的 GET。

    节流收口在这里：每次请求前保证距上一次请求至少 delay 秒，
    调用方无需再自行 sleep（也避免了 --full 模式下实际间隔只有宣称一半的问题）。
    404/410/400/403 等永久性错误不重试，只对瞬时失败做退避 + 抖动。
    """
    global _LAST_REQUEST
    last_err = None
    for i in range(retries + 1):
        with _THROTTLE_LOCK:
            elapsed = time.monotonic() - _LAST_REQUEST
            if elapsed < delay:
                time.sleep(delay - elapsed)
            _LAST_REQUEST = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in {400, 403, 404, 410}:
                raise RuntimeError(f"GET {url} 失败: HTTP {e.code}（永久性错误，不重试）") from e
            last_err = e
        except Exception as e:
            last_err = e
        if i < retries:
            time.sleep(2.5 * (i + 1) + random.uniform(0, 1.0))
    raise RuntimeError(f"GET {url} 失败: {last_err}")


# ----------------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------------
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(s)).strip()


def parse_journal_page(html: str):
    """解析期刊首页/某期页，返回 (年份列表[降序], 各年期的映射, 当前年/期)。"""
    years = []
    m = re.search(r'collection_list" val="([^"]*)" id="([^"]*)" year="([^"]*)"', html)
    if m:
        raw = unescape(m.group(1)).replace("&quot;", '"')
        try:
            years = json.loads(raw)
            years = [int(y) for y in years]
        except Exception:
            years = []
        years.sort(reverse=True)

    issue_list = {}
    mi = re.search(r'issueList=(\{.*?\});', html, re.S)
    if mi:
        raw = mi.group(1).replace("&quot;", '"')
        try:
            issue_list = json.loads(raw)
        except Exception:
            issue_list = {}

    active_year = m.group(3) if m else ""
    return years, issue_list, active_year


def parse_article_items(html: str):
    """从某期列表页解析所有文章条目。"""
    articles = []
    for block in re.finditer(r'<div class="article_item">(.*?)</div>\s*</div>\s*</div>', html, re.S):
        b = block.group(1)
        # 标题 + 详情页 URL / 文章 ID
        title = ""
        url = ""
        mh = re.search(r'<h4[^>]*><a[^>]*href="([^"]+)"[^>]*title="([^"]*)"', b)
        if mh:
            url = unescape(mh.group(1))
            title = _clean(mh.group(2))
        if not title:
            mh = re.search(r'<h4[^>]*>.*?</h4>', b, re.S)
            if mh:
                title = _clean(re.sub(r"<[^>]+>", "", mh.group(0)))
        # 不把 "010" 写死：平台路径前缀变化时 article_id 仍能提取，保证去重键稳定。
        # 兼容 /periodical/010<id> 与 /periodical/<path>/<id> 两种形式。
        mid = re.search(r"/periodical/(?:010)?([a-z0-9]+)/?$", url)
        article_id = mid.group(1) if mid else ""
        if not article_id:
            segments = [seg for seg in url.rstrip("/").split("/") if seg]
            if segments and re.fullmatch(r"[a-z0-9-]+", segments[-1]):
                article_id = segments[-1]
        # 作者
        authors = [a.strip() for a in re.findall(r'<span class="highLight"[^>]*>(.*?)</span>', b)]
        authors = [a for a in authors if a]
        # 页码
        pages = ""
        mp = re.search(r'class="submissionPage">([^<]+)</span>', b)
        if mp:
            pages = _clean(mp.group(1))
        # 中文摘要
        abstract_cn = ""
        ma = re.search(r'abstracLabel">摘要：</span><span class="ignore">(.*?)</span>', b, re.S)
        if ma:
            abstract_cn = _clean(ma.group(1))
        else:
            ma = re.search(r'class="outBox">.*?摘要.*?<span class="ignore">(.*?)</span>', b, re.S)
            if ma:
                abstract_cn = _clean(ma.group(1))
        if article_id or title:
            articles.append({
                "source": "napstic",
                "source_id": article_id,
                "title_cn": title,
                "authors_cn": authors,
                "pages": pages,
                "abstract_cn": abstract_cn,
                "detail_url": url or None,
            })
    return articles


def parse_detail(html: str):
    """从文章详情页补全字段：英文标题/摘要、关键词、单位、DOI、卷期信息。"""
    d = {}
    def grab(pattern, flags=re.S):
        m = re.search(pattern, html, flags)
        return _clean(m.group(1)) if m else ""

    d["title_cn"] = grab(r'id="titleCN"[^>]*>(.*?)</h1>')
    d["title_en"] = grab(r'id="titleEN"[^>]*>(.*?)</h2>')
    d["abstract_cn"] = grab(r'id="abstractCN"[^>]*>.*?<span class="ignore">(.*?)</span>')
    d["abstract_en"] = grab(r'id="abstractEN"[^>]*>.*?<span class="ignore">(.*?)</span>')
    d["keywords_cn"] = [k.strip() for k in re.findall(r'<i class="keyword_cn"[^>]*>(.*?)</i>', html, re.S)]
    d["keywords_en"] = [k.strip() for k in re.findall(r'<i class="keyword_en"[^>]*>(.*?)</i>', html, re.S)]

    # 作者：页面里同一作者出现两次（概览区无角标 + 作者区带角标），按去掉尾部数字角标后的名字去重
    authors = []
    seen = set()
    for a in re.findall(r'<span class="name">(.*?)</span>', html, re.S):
        a = _clean(re.sub(r"<[^>]+>", " ", a)).rstrip()
        base = re.sub(r"[\d\s]+$", "", a)
        if base and base not in seen:
            seen.add(base)
            authors.append(base)
    d["authors_cn"] = authors

    # 单位：去掉 <i> 序号标签
    d["affiliations_cn"] = []
    for a in re.findall(r'<li class="author_unit">(.*?)</li>', html, re.S):
        a = _clean(re.sub(r"<[^>]+>", " ", a)).lstrip("0123456789. ")
        if a and a not in d["affiliations_cn"]:
            d["affiliations_cn"].append(a)

    d["doi"] = grab(r'class="periodical-import">([^<]+)') or grab(r'periodical-doi">DOI:\s*([^<]+)')

    # 期刊/卷期行（例：<span class="...periodical-year">2024，</span>...Vol.</span><span>44</span>...Issue</span><span>(17) ：</span><span>6707-6720.</span>）
    mj = re.search(r'periodical-year">(20\d\d)', html)
    mv = re.search(r'periodical-volume">Vol\.</span><span>(\d+)', html)
    mi2 = re.search(r'Issue</span><span>\(?(\d+)\)?', html)
    mp = re.search(r'(\d{2,5}\s*-\s*\d{2,5})\.</span>', html)
    if mj:
        d["year"] = mj.group(1)
    if mv:
        d["volume"] = mv.group(1)
    if mi2:
        d["issue"] = mi2.group(1)
    if mp:
        d["pages"] = mp.group(1)
    return {k: v for k, v in d.items() if v}


# ----------------------------------------------------------------------------
# 关键词检索（opaj.napstic.cn 检索接口，数据源含万方，覆盖比 OA 期刊页新）
# ----------------------------------------------------------------------------
SEARCH_URL = "https://opaj.napstic.cn/search/literature"


def search_literature(query: str, size: int = 20, page: int = 1, delay: float = 1.2):
    """按关键词检索全部中文期刊，返回 (记录列表, 命中总数)。

    检索接口字段比 OA 期刊页新（含 date.online 网络首发日期），并带 perioInfoId（期刊 slug）。
    """
    url = (f"{SEARCH_URL}?q={urllib.parse.quote(query)}&pageNo={page}&size={size}"
           "&resourceTypes=periodicalArticle")
    d = json.loads(http_get(url, delay=delay))
    sr = d.get("result", {}).get("searchResult", {})
    total = sr.get("total", 0)
    records = []
    for it in sr.get("list", []):
        pub = it.get("publication") or {}
        title = it.get("title") or {}
        ab = it.get("abstract") or {}
        u = it.get("url") or {}
        date = it.get("date") or {}
        ident = it.get("identifier") or {}
        creators = []
        for c in it.get("creators") or []:
            n = c.get("name")
            org = c.get("organization")
            if n:
                creators.append(n + (f" ({org})" if org else ""))
        records.append({
            "source": "napstic-search",
            "source_id": ident.get("articleId") or ident.get("id") or "",
            "title_cn": title.get("zh") or title.get("raw") or "",
            "title_en": title.get("en") or "",
            "abstract_cn": ab.get("zh") or ab.get("raw") or "",
            "authors_cn": creators,
            "journal_cn": (pub.get("name") or {}).get("zh") or "",
            "journal_en": (pub.get("name") or {}).get("en") or "",
            "issn": pub.get("issn") or "",
            "year": str(pub.get("year") or ""),
            "volume": str(pub.get("volume") or ""),
            "issue": str(pub.get("issue") or ""),
            "pages": str(pub.get("page") or ""),
            "doi": u.get("doi") or "",
            "online_date": str(date.get("online") or "").strip(),
            "detail_url": u.get("detail") or "",
        })
    return records, total


# ----------------------------------------------------------------------------
# 抓取
# ----------------------------------------------------------------------------
def fetch_issue(slug: str, year: int, issue, fetch_details: bool = False, delay: float = 1.2, max_pages: int = 30):
    """抓某一期的全部文章。返回 (articles, 该期信息)。

    翻页以「页面解析到 0 篇」为终止条件，不再假设每页固定 10 条
    （平台改每页条数时旧逻辑会静默漏抓后半期）。
    """
    jinfo = JOURNALS.get(slug, {})
    articles, page = [], 1
    while page <= max_pages:
        url = (f"{BASE}/literature/oaj/{slug}?page={page}&activeYear={year}&activeIssue={issue}")
        html = http_get(url, delay=delay)
        items = parse_article_items(html)
        if not items:
            break
        articles.extend(items)
        page += 1
    # 补全元信息（节流由 http_get 统一保证，这里不再额外 sleep）
    detail_failed = 0
    for a in articles:
        a.setdefault("journal_cn", jinfo.get("name_cn", ""))
        a.setdefault("journal_en", jinfo.get("name_en", ""))
        a.setdefault("issn", jinfo.get("issn", ""))
        a.setdefault("year", str(year))
        a.setdefault("issue", str(issue))
        if fetch_details and a.get("detail_url"):
            try:
                det = parse_detail(http_get(a["detail_url"], delay=delay))
                a.update(det)
                a.setdefault("url", a["detail_url"])
            except Exception as e:  # noqa: BLE001 — 单篇详情失败不影响整期
                detail_failed += 1
                print(f"  [warn] {slug} 详情补全失败 ({str(a.get('title_cn', ''))[:40]}): {e}", file=sys.stderr)
    if detail_failed:
        print(f"  [warn] {slug} {year}-{issue}: {detail_failed}/{len(articles)} 篇详情补全失败", file=sys.stderr)
    return articles


def fetch_recent(slug: str, months: int = 6, fetch_details: bool = False, delay: float = 1.2):
    """抓最近 months 个月内的文章（按期数估算：假设一年 N 期，最近 M 个月 ≈ ceil(N*M/12) 期）。"""
    page_html = http_get(f"{BASE}/literature/oaj/{slug}?page=1", delay=delay)
    years, issue_list, active_year = parse_journal_page(page_html)
    if not years:
        raise RuntimeError(f"{slug}: 无法解析期刊页年份信息，请确认 slug 正确（用 `list` 子命令）。")

    # 用最近一个完整年的期数估算一年几期
    issues_per_year = 0
    for y in years:
        n = len(issue_list.get(str(y), []))
        if n > issues_per_year:
            issues_per_year = n
    need = max(1, int(round(issues_per_year * max(months, 1) / 12.0)))
    print(f"  {slug}: 估算年 {issues_per_year} 期 -> 最近 {months} 个月取最新 {need} 期", file=sys.stderr)

    # 从最新一期往前，跨年份累计 need 期（期号可能是增刊如 "Z1"，需安全排序）
    def _issue_num(i):
        m = re.match(r"(\d+)", str(i))
        return int(m.group(1)) if m else 0

    queue = []
    for y in sorted(years, reverse=True):
        issues = [it.get("issue") for it in issue_list.get(str(y), []) if it.get("issue")]
        for iss in sorted(issues, key=_issue_num, reverse=True):
            queue.append((y, iss))
    queue = queue[:need]

    articles = []
    fetched = []
    for (y, iss) in queue:
        try:
            arts = fetch_issue(slug, y, iss, fetch_details=fetch_details, delay=delay)
            fetched.append((y, iss, len(arts)))
            articles.extend(arts)
        except Exception as e:
            print(f"  跳过 {y}-{iss}: {e}", file=sys.stderr)
    return articles, fetched


# ----------------------------------------------------------------------------
# 输出
# ----------------------------------------------------------------------------
def save(articles, out):
    if out == "-":
        print(json.dumps(articles, ensure_ascii=False, indent=2))
    else:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        print(f"已写入 {out}: {len(articles)} 篇")


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="国家学术搜索(NAPSTIC) 中文期刊题录+摘要抓取")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="列出某期刊可用的年份/期数")
    p_list.add_argument("slug")

    p_fetch = sub.add_parser("fetch", help="抓取某一期")
    p_fetch.add_argument("slug")
    p_fetch.add_argument("--year", required=True)
    p_fetch.add_argument("--issue", required=True)
    p_fetch.add_argument("--full", action="store_true", help="逐篇抓详情页（含英文摘要/DOI/关键词）")
    p_fetch.add_argument("--out", default="out.json")
    p_fetch.add_argument("--delay", type=float, default=1.2)

    p_recent = sub.add_parser("recent", help="抓最近几个月")
    p_recent.add_argument("slug")
    p_recent.add_argument("--months", type=int, default=6)
    p_recent.add_argument("--full", action="store_true")
    p_recent.add_argument("--out", default="recent.json")
    p_recent.add_argument("--delay", type=float, default=1.2)

    p_search = sub.add_parser("search", help="按关键词检索全部中文期刊（含网络首发，更新）")
    p_search.add_argument("query", help="检索关键词，如 构网型 / 储能 / 新型电力系统")
    p_search.add_argument("--size", type=int, default=20)
    p_search.add_argument("--pages", type=int, default=1, help="抓多少页（每页 size 条）")
    p_search.add_argument("--journal", help="只保留包含该刊名的结果（客户端过滤）")
    p_search.add_argument("--out", default="search.json")
    p_search.add_argument("--delay", type=float, default=1.2)

    p_all = sub.add_parser("recent-all", help="抓配置里所有期刊最近几个月")
    p_all.add_argument("--months", type=int, default=3)
    p_all.add_argument("--full", action="store_true")
    p_all.add_argument("--out", default="recent_all.json")
    p_all.add_argument("--delay", type=float, default=1.2)

    args = ap.parse_args()

    if args.cmd == "list":
        html = http_get(f"{BASE}/literature/oaj/{args.slug}?page=1")
        years, issue_list, active_year = parse_journal_page(html)
        if not years:
            print(f"{args.slug}: 未找到有效期刊页（slug 可能不对）。")
            sys.exit(1)
        print(f"{args.slug}  可用年份: {years[0]} ~ {years[-1]}  当前年: {active_year}")
        for y in years[:3]:
            issues = [it.get("issue") for it in issue_list.get(str(y), []) if it.get("issue")]
            print(f"  {y}: 共 {len(issues)} 期 -> {','.join(issues[:12])}{'…' if len(issues)>12 else ''}")

    elif args.cmd == "fetch":
        arts = fetch_issue(args.slug, args.year, args.issue, fetch_details=args.full, delay=args.delay)
        save(arts, args.out)
        print(f"提示: 列表页模式仅含中文摘要; 加 --full 可补英文摘要/DOI/关键词。")

    elif args.cmd == "recent":
        arts, fetched = fetch_recent(args.slug, months=args.months, fetch_details=args.full, delay=args.delay)
        save(arts, args.out)
        print("抓取明细 (年/期/篇数):", fetched)

    elif args.cmd == "search":
        all_recs, total = [], 0
        for p in range(1, args.pages + 1):
            try:
                recs, total = search_literature(args.query, size=args.size, page=p, delay=args.delay)
                all_recs.extend(recs)
            except Exception as e:  # noqa: BLE001 — 单页失败不拖垮整条命令
                print(f"[warn] 第 {p} 页检索失败: {e}", file=sys.stderr)
        if args.journal:
            all_recs = [r for r in all_recs if args.journal in (r.get("journal_cn") or "")]
        save(all_recs, args.out)
        print(f"检索 '{args.query}': 平台命中 {total} 篇，本次返回 {len(all_recs)} 篇（含摘要/网络首发日期）。")

    elif args.cmd == "recent-all":
        all_arts = []
        for slug in JOURNALS:
            print(f"\n=== {slug} ===")
            try:
                arts, fetched = fetch_recent(slug, months=args.months, fetch_details=args.full, delay=args.delay)
                all_arts.extend(arts)
                print("  明细:", fetched)
            except Exception as e:
                print("  失败:", e)
            time.sleep(args.delay)
        save(all_arts, args.out)


if __name__ == "__main__":
    main()
