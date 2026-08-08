#!/usr/bin/env python3
"""Power-system academic radar runner.

The JSON config path works with only the Python standard library. YAML configs
require PyYAML. Network sources are optional; manual export ingestion works
offline.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import http.client
import json
import os
import re
import smtplib
import ssl
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable, Iterator


# cn_napstic 是本仓库自带模块（scripts/cn_napstic.py）；缺失时对应源只告警跳过，不影响其他源。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import cn_napstic  # noqa: E402
except ImportError:  # pragma: no cover
    cn_napstic = None


USER_AGENT = "power-system-academic-radar/0.1 (+local Codex plugin)"
LAST_SEMANTIC_SCHOLAR_REQUEST = 0.0


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def load_config(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    data = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(data)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "YAML config requires PyYAML. Install scripts/requirements.txt "
                "or use assets/power_system_radar_config.json."
            ) from exc
        return yaml.safe_load(data)
    raise SystemExit(f"Unsupported config format: {path}")


def resolve_path(value: str | None, root: Path) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def http_json(url: str, timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return read_http_json(req, timeout)


def http_json_headers(url: str, headers: dict[str, str], timeout: int = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})
    return read_http_json(req, timeout)


def read_http_json(request: urllib.request.Request, timeout: int, attempts: int = 2) -> dict[str, Any]:
    """读取 JSON 响应；传输被截断时重试一次，其余 HTTP 错误保持原语义。"""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except http.client.IncompleteRead:
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.5)
    raise RuntimeError("unreachable")


def semantic_scholar_json(url: str, api_key: str = "", timeout: int = 30, min_interval: float = 1.2) -> dict[str, Any]:
    global LAST_SEMANTIC_SCHOLAR_REQUEST
    elapsed = time.monotonic() - LAST_SEMANTIC_SCHOLAR_REQUEST
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    headers = {"x-api-key": api_key} if api_key else {}
    try:
        return http_json_headers(url, headers=headers, timeout=timeout)
    finally:
        LAST_SEMANTIC_SCHOLAR_REQUEST = time.monotonic()


def http_text(url: str, timeout: int = 30, verify_ssl: bool = True) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = None if verify_ssl else ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_text_headers(url: str, headers: dict[str, str], timeout: int = 30, verify_ssl: bool = True) -> str:
    req = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})
    context = None if verify_ssl else ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 60) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers, "User-Agent": headers.get("User-Agent") or USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def post_webhook(url: str, payload: dict[str, Any], timeout: int = 20) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout):
        return


def env_value(name: str | None, default: str = "") -> str:
    return os.environ.get(name or "", default)


def normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_doi(value: Any) -> str:
    text = normalize_space(value).lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    return text


def item_key(item: dict[str, Any]) -> str:
    doi = normalize_doi(item.get("doi"))
    if doi:
        return f"doi:{doi}"
    source_id = normalize_space(item.get("source_id"))
    if source_id:
        return "sid:" + hashlib.sha1(source_id.encode("utf-8")).hexdigest()
    title = normalize_space(item.get("title")).lower()
    return "title:" + hashlib.sha1(title.encode("utf-8")).hexdigest()


def normalized_title_key(item: dict[str, Any]) -> str:
    title = normalize_space(item.get("title")).lower()
    title = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", title)
    if len(title) < 12:
        return ""
    return "title-normalized:" + hashlib.sha1(title.encode("utf-8")).hexdigest()


def split_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_space(v) for v in value if normalize_space(v)]
    text = normalize_space(value)
    if not text:
        return []
    return [p.strip() for p in re.split(r";|, and | and |\|", text) if p.strip()]


def split_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_space(v) for v in value if normalize_space(v)]
    text = normalize_space(value)
    if not text:
        return []
    return [p.strip() for p in re.split(r";|,|\||/|；|，", text) if p.strip()]


def normalize_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = normalize_space(value).lower()
    if text in {"1", "true", "yes", "y", "oa", "open", "open access", "开放", "是"}:
        return True
    if text in {"0", "false", "no", "n", "closed", "closed access", "否"}:
        return False
    return None


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


def clean_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": normalize_space(item.get("title")),
        "title_en": normalize_space(item.get("title_en")),
        "authors": split_authors(item.get("authors")),
        "year": normalize_space(item.get("year")),
        "published": normalize_space(item.get("published")),
        "venue": normalize_space(item.get("venue")),
        "doi": normalize_doi(item.get("doi")),
        "url": normalize_space(item.get("url")),
        "abstract": normalize_space(item.get("abstract")),
        "abstract_zh": normalize_space(item.get("abstract_zh")),
        "abstract_en": normalize_space(item.get("abstract_en")),
        "keywords": split_keywords(item.get("keywords")),
        "source": normalize_space(item.get("source")),
        "origin": normalize_space(item.get("origin")),
        "source_id": normalize_space(item.get("source_id")),
        "is_oa": normalize_bool(item.get("is_oa")),
        "oa_url": normalize_space(item.get("oa_url")),
        "access_status": normalize_space(item.get("access_status")),
        "publication_type": normalize_space(item.get("publication_type")),
        "venue_type": normalize_space(item.get("venue_type")),
    }


CHINESE_QUERY_TYPES = {"cnki", "manual_chinese", "napstic_search", "napstic_journals"}


def build_default_query(config: dict[str, Any], source_type: str) -> str:
    queries = config.get("queries") or {}
    if queries.get("auto_from_keywords"):
        keywords = config.get("keywords") or {}

        def quoted_terms(groups: Iterable[str]) -> list[str]:
            terms: list[str] = []
            seen: set[str] = set()
            for group in groups:
                for raw_term in keywords.get(group, []):
                    term = normalize_space(raw_term)
                    key = term.casefold()
                    if not term or key in seen:
                        continue
                    seen.add(key)
                    terms.append(f'"{term}"' if " " in term else term)
            return terms

        if source_type in CHINESE_QUERY_TYPES:
            return " OR ".join(quoted_terms(["chinese"]))

        # UI 管理的关键词分成“研究对象”和“研究问题/方法”两侧，避免退化成过宽的 OR 检索。
        core = quoted_terms(["core"])
        focus_groups = [name for name in keywords if name not in {"core", "chinese", "exclude"}]
        focus = quoted_terms(focus_groups)
        if core and focus:
            expression = f"({' OR '.join(core)}) AND ({' OR '.join(focus)})"
        else:
            expression = " OR ".join(core or focus)
        if expression:
            if source_type == "elsevier_scopus_api":
                return f"TITLE-ABS-KEY({expression})"
            if source_type == "arxiv":
                return f"all:({expression})"
            return expression
    if source_type in CHINESE_QUERY_TYPES:
        return queries.get("chinese") or " ".join(config["keywords"].get("chinese", []))
    return queries.get("english") or " ".join(config["keywords"].get("core", []))


def source_query(config: dict[str, Any], source: dict[str, Any]) -> str:
    if (config.get("queries") or {}).get("auto_from_keywords"):
        return build_default_query(config, source.get("type", ""))
    return source.get("query_override") or build_default_query(config, source.get("type", ""))


def fetch_openalex(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    params = {
        "search": source_query(config, source),
        "filter": f"from_publication_date:{since.isoformat()}",
        "per-page": str(limit),
        "sort": "publication_date:desc",
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = http_json(url)
    items = []
    for work in data.get("results", []):
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in work.get("authorships", [])
        ]
        primary = work.get("primary_location") or {}
        source_meta = primary.get("source") or {}
        open_access = work.get("open_access") or {}
        concept_names = [c.get("display_name", "") for c in work.get("concepts", []) if c.get("display_name")]
        items.append(
            clean_item(
                {
                    "title": work.get("title"),
                    "authors": authors,
                    "year": work.get("publication_year"),
                    "published": work.get("publication_date"),
                    "venue": source_meta.get("display_name"),
                    "doi": work.get("doi"),
                    "url": work.get("id"),
                    "abstract": inverted_abstract(work.get("abstract_inverted_index")),
                    "keywords": concept_names,
                    "source": source["name"],
                    "origin": "openalex",
                    "is_oa": open_access.get("is_oa"),
                    "oa_url": open_access.get("oa_url") or primary.get("pdf_url") or primary.get("landing_page_url"),
                    "access_status": open_access.get("oa_status"),
                    "publication_type": work.get("type"),
                    "venue_type": source_meta.get("type"),
                }
            )
        )
    return items


def inverted_abstract(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, locs in index.items():
        for loc in locs:
            positions.append((int(loc), word))
    return " ".join(word for _, word in sorted(positions))


def fetch_crossref(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    params = {
        "query.bibliographic": source_query(config, source),
        "filter": f"from-pub-date:{since.isoformat()}",
        "rows": str(limit),
        "sort": "published",
        "order": "desc",
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = http_json(url)
    items = []
    for work in (data.get("message") or {}).get("items", []):
        authors = [
            normalize_space(f"{a.get('given', '')} {a.get('family', '')}")
            for a in work.get("author", [])
        ]
        date_parts = (((work.get("published-print") or work.get("published-online") or work.get("created") or {}).get("date-parts") or [[]])[0])
        published = "-".join(str(x) for x in date_parts if x)
        links = work.get("link") or []
        licenses = work.get("license") or []
        oa_url = next((link.get("URL") for link in links if link.get("URL")), "")
        items.append(
            clean_item(
                {
                    "title": " ".join(work.get("title") or []),
                    "authors": authors,
                    "year": date_parts[0] if date_parts else "",
                    "published": published,
                    "venue": " ".join(work.get("container-title") or []),
                    "doi": work.get("DOI"),
                    "url": work.get("URL"),
                    "abstract": strip_tags(work.get("abstract", "")),
                    "keywords": work.get("subject") or [],
                    "source": source["name"],
                    "origin": "crossref",
                    "is_oa": bool(licenses or oa_url),
                    "oa_url": oa_url,
                    "access_status": "open" if licenses or oa_url else "",
                    "publication_type": work.get("type"),
                }
            )
        )
    return items


def fetch_arxiv(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    params = {
        "search_query": source_query(config, source),
        "start": "0",
        "max_results": str(limit),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    # arXiv sometimes fails because of local CA-store or endpoint policy issues.
    # Try configurable endpoints in order, then let the caller log a single source warning.
    bases = source.get("base_urls") or [
        "http://export.arxiv.org/api/query",
        "https://export.arxiv.org/api/query",
    ]
    root = None
    last_error: Exception | None = None
    for base in bases:
        try:
            url = str(base) + "?" + urllib.parse.urlencode(params)
            root = ET.fromstring(
                http_text(
                    url,
                    timeout=int(source.get("timeout_seconds", 30)),
                    verify_ssl=bool(source.get("verify_ssl", False)),
                )
            )
            break
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            last_error = exc
    if root is None:
        raise last_error or RuntimeError("arXiv API failed")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    for entry in root.findall("a:entry", ns):
        published = text_of(entry, "a:published", ns)[:10]
        if published and published < since.isoformat():
            continue
        authors = [text_of(a, "a:name", ns) for a in entry.findall("a:author", ns)]
        items.append(
            clean_item(
                {
                    "title": text_of(entry, "a:title", ns),
                    "authors": authors,
                    "year": published[:4],
                    "published": published,
                    "venue": "arXiv",
                    "doi": text_of(entry, "a:doi", ns),
                    "url": text_of(entry, "a:id", ns),
                    "abstract": text_of(entry, "a:summary", ns),
                    "source": source["name"],
                    "origin": "arxiv",
                    "is_oa": True,
                    "oa_url": text_of(entry, "a:id", ns),
                    "access_status": "open",
                }
            )
        )
    return items


def fetch_semantic_scholar(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    params = {
        "query": source_query(config, source),
        "limit": str(min(limit, 100)),
        "fields": "title,authors,year,venue,publicationDate,publicationTypes,externalIds,url,abstract,openAccessPdf,fieldsOfStudy,s2FieldsOfStudy",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    api_key = os.environ.get(source.get("api_key_env", "SEMANTIC_SCHOLAR_API_KEY"))
    min_interval = float(source.get("min_interval_seconds", (config.get("abstract_enrichment") or {}).get("semantic_scholar_min_interval_seconds", 1.2)))
    try:
        data = semantic_scholar_json(url, api_key=api_key or "", timeout=30, min_interval=min_interval)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            wait_msg = f"; retry after {retry_after}s" if retry_after else ""
            key_msg = "set SEMANTIC_SCHOLAR_API_KEY to reduce rate limits" if not api_key else "try later or reduce max_results"
            print(f"[warn] semantic_scholar skipped: HTTP 429 rate limited{wait_msg}; {key_msg}", file=sys.stderr)
            return []
        raise
    items = []
    for paper in data.get("data", []):
        published = normalize_space(paper.get("publicationDate"))
        if published and published[:10] < since.isoformat():
            continue
        external = paper.get("externalIds") or {}
        oa_pdf = paper.get("openAccessPdf") or {}
        s2_fields = [f.get("category", "") for f in paper.get("s2FieldsOfStudy") or [] if f.get("category")]
        items.append(
            clean_item(
                {
                    "title": paper.get("title"),
                    "authors": [a.get("name", "") for a in paper.get("authors", [])],
                    "year": paper.get("year"),
                    "published": published,
                    "venue": paper.get("venue"),
                    "doi": external.get("DOI"),
                    "url": oa_pdf.get("url") or paper.get("url"),
                    "abstract": paper.get("abstract"),
                    "keywords": (paper.get("fieldsOfStudy") or []) + s2_fields,
                    "source": source["name"],
                    "origin": "semantic_scholar",
                    "is_oa": bool(oa_pdf.get("url")),
                    "oa_url": oa_pdf.get("url"),
                    "access_status": "open" if oa_pdf.get("url") else "",
                    "publication_type": " ".join(paper.get("publicationTypes") or []),
                }
            )
        )
    return items


def fetch_ieee(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    key = os.environ.get(source.get("api_key_env", "IEEE_XPLORE_API_KEY"))
    if not key:
        print(f"[warn] {source['name']} skipped: missing API key env", file=sys.stderr)
        return []
    params = {
        "apikey": key,
        "format": "json",
        "max_records": str(limit),
        "sort_order": "desc",
        "sort_field": "publication_date",
        "querytext": source_query(config, source),
        "start_year": str(since.year),
        "end_year": str(utc_today().year),
    }
    url = "https://ieeexploreapi.ieee.org/api/v1/search/articles?" + urllib.parse.urlencode(params)
    try:
        data = http_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            print(
                f"[warn] {source['name']} skipped: HTTP {exc.code} from IEEE Xplore API. "
                "Check that the application is approved for Metadata Search API, "
                "the IEEE_XPLORE_API_KEY value is copied exactly, and the key is active.",
                file=sys.stderr,
            )
            return []
        if exc.code == 429:
            print(f"[warn] {source['name']} skipped: IEEE Xplore API rate limited; try later", file=sys.stderr)
            return []
        raise
    items = []
    for article in data.get("articles", []):
        items.append(
            clean_item(
                {
                    "title": article.get("title"),
                    "authors": [a.get("full_name", "") for a in article.get("authors", {}).get("authors", [])],
                    "year": article.get("publication_year"),
                    "published": article.get("publication_date"),
                    "venue": article.get("publication_title"),
                    "doi": article.get("doi"),
                    "url": article.get("html_url") or article.get("abstract_url"),
                    "abstract": article.get("abstract"),
                    "source": source["name"],
                    "origin": "ieee_xplore_api",
                    "publication_type": article.get("content_type"),
                }
            )
        )
    return items


def fetch_elsevier_abstract(api_key: str, doi: str, eid: str, timeout: int = 30) -> str:
    identifier_path = ""
    if doi:
        identifier_path = "doi/" + urllib.parse.quote(doi, safe="")
    elif eid:
        identifier_path = "eid/" + urllib.parse.quote(eid, safe="")
    if not identifier_path:
        return ""
    url = "https://api.elsevier.com/content/abstract/" + identifier_path
    params = {"view": "FULL"}
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    try:
        data = http_json_headers(url + "?" + urllib.parse.urlencode(params), headers=headers, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404, 429}:
            return ""
        raise
    response = data.get("abstracts-retrieval-response") or data.get("abstract-response") or {}
    coredata = response.get("coredata") or {}
    return normalize_space(coredata.get("dc:description") or coredata.get("description") or "")


def elsevier_xml_abstract(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        match = re.search(r"<[^>/]*abstract\b[^>]*>(.*?)</[^>]*abstract>", xml_text, flags=re.I | re.S)
        return strip_tags(match.group(1)) if match else ""

    # Elsevier article XML normally stores the abstract in a namespaced
    # <ce:abstract> subtree; older/metadata responses may expose dc:description.
    for preferred in ("abstract", "description"):
        for element in root.iter():
            if local_name(element.tag).lower() != preferred:
                continue
            text = element_text(element)
            if len(text) >= 40:
                return text
    return ""


def fetch_elsevier_article_abstract(api_key: str, item: dict[str, Any], timeout: int = 30) -> str:
    doi = normalize_doi(item.get("doi"))
    candidates: list[str] = []
    for key in ("oa_url", "url"):
        url = normalize_space(item.get(key))
        if "api.elsevier.com/content/article" in url:
            candidates.append(url)
    if doi:
        candidates.append(
            "https://api.elsevier.com/content/article/doi/"
            + urllib.parse.quote(doi, safe="")
            + "?httpAccept=text/xml"
        )

    seen: set[str] = set()
    headers = {"X-ELS-APIKey": api_key, "Accept": "text/xml"}
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            xml_text = http_text_headers(url, headers=headers, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 404, 429}:
                continue
            raise
        abstract = elsevier_xml_abstract(xml_text)
        if abstract:
            return abstract
    return ""


def fetch_elsevier_scopus(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    api_key = os.environ.get(source.get("api_key_env", "ELSEVIER_API_KEY"))
    if not api_key:
        print(f"[warn] {source['name']} skipped: missing API key env", file=sys.stderr)
        return []
    query = source_query(config, source)
    if "PUBYEAR" not in query.upper():
        query = f"({query}) AND PUBYEAR AFT {since.year - 1}"
    params = {
        "query": query,
        "count": str(min(limit, 25)),
        "start": "0",
        "sort": "-coverDate",
        "view": "STANDARD",
    }
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}
    url = "https://api.elsevier.com/content/search/scopus?" + urllib.parse.urlencode(params)
    try:
        data = http_json_headers(url, headers=headers, timeout=int(source.get("timeout_seconds", 30)))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            print(
                f"[warn] {source['name']} skipped: HTTP {exc.code} from Elsevier/Scopus API. "
                "Check ELSEVIER_API_KEY permissions and whether Scopus APIs are enabled for the key.",
                file=sys.stderr,
            )
            return []
        if exc.code == 429:
            print(f"[warn] {source['name']} skipped: Elsevier/Scopus API rate limited; try later", file=sys.stderr)
            return []
        raise
    entries = (data.get("search-results") or {}).get("entry") or []
    items = []
    max_abstracts = int(source.get("max_abstract_enrichment", 10))
    for entry in entries:
        published = normalize_space(entry.get("prism:coverDate"))
        if published and published[:10] < since.isoformat():
            continue
        doi = normalize_doi(entry.get("prism:doi"))
        eid = normalize_space(entry.get("eid"))
        abstract = ""
        if max_abstracts > 0:
            abstract = fetch_elsevier_abstract(api_key, doi, eid, timeout=int(source.get("timeout_seconds", 30)))
            max_abstracts -= 1
        open_flag = normalize_bool(entry.get("openaccessFlag") or entry.get("openaccess"))
        items.append(
            clean_item(
                {
                    "title": entry.get("dc:title"),
                    "authors": entry.get("dc:creator"),
                    "year": published[:4],
                    "published": published,
                    "venue": entry.get("prism:publicationName"),
                    "doi": doi,
                    "url": entry.get("prism:url") or entry.get("link"),
                    "abstract": abstract,
                    "keywords": [entry.get("subtypeDescription"), entry.get("prism:aggregationType")],
                    "source": source["name"],
                    "origin": "elsevier_scopus_api",
                    "is_oa": open_flag,
                    "access_status": "open" if open_flag else "",
                    "publication_type": entry.get("subtypeDescription") or entry.get("subtype"),
                    "venue_type": entry.get("prism:aggregationType"),
                }
            )
        )
    return items


def fetch_rss(config: dict[str, Any], source: dict[str, Any], since: dt.date, limit: int) -> list[dict[str, Any]]:
    items = []
    for url in source.get("urls", []):
        if "example.com" in url:
            continue
        try:
            root = ET.fromstring(http_text(url))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] rss failed {url}: {exc}", file=sys.stderr)
            continue
        for entry in iter_feed_entries(root):
            item = clean_item({**entry, "source": source["name"], "origin": url})
            if keyword_score(item, config)[0] >= int(config.get("scoring", {}).get("min_score", 4)):
                items.append(item)
            if len(items) >= limit:
                break
    return items[:limit]


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
    """按 chinese 关键词组逐词检索 NAPSTIC（含网络首发）。

    该检索接口只支持相关度排序、无法按日期过滤或排序（实测 sort/order 参数均被忽略），
    因此不按 since 窗口硬过滤：客户端按 online_date 降序（无日期沉底），重复由状态文件去重。
    """
    if cn_napstic is None:
        print(f"[warn] {source['name']} skipped: cn_napstic module not found", file=sys.stderr)
        return []
    terms = napstic_query_terms(config, source)
    if not terms:
        print(f"[warn] {source['name']} skipped: empty query", file=sys.stderr)
        return []
    size = max(1, int(source.get("size", 20)))
    delay = float(source.get("delay_seconds", 1.5))
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
    unique: list[dict[str, Any]] = []
    for rec in records:
        doi = normalize_doi(rec.get("doi"))
        rec_key = doi or normalize_space(rec.get("source_id"))
        if rec_key and rec_key in seen_keys:
            continue
        if rec_key:
            seen_keys.add(rec_key)
        unique.append(rec)
    # 网络首发最新在前；无 online_date 的沉底（空串自然小于任意日期）
    unique.sort(key=lambda rec: rec.get("online_date") or "", reverse=True)
    items = []
    bypass = bool(source.get("bypass_journal_whitelist", False))
    for rec in unique[:limit]:
        item = clean_item(napstic_to_item(rec, source))
        if bypass:
            item["bypass_journal_whitelist"] = True
        items.append(item)
    print(f"[info] {source['name']} terms={len(terms)}: platform hit {total}, kept {len(items)}", file=sys.stderr)
    return items


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


def iter_feed_entries(root: ET.Element) -> Iterable[dict[str, Any]]:
    atom_ns = {"a": "http://www.w3.org/2005/Atom"}
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            yield {
                "title": child_text(item, "title"),
                "authors": child_text(item, "author") or child_text(item, "dc:creator"),
                "published": child_text(item, "pubDate"),
                "venue": child_text(channel, "title"),
                "url": child_text(item, "link"),
                "abstract": child_text(item, "description"),
            }
    for entry in root.findall("a:entry", atom_ns):
        yield {
            "title": text_of(entry, "a:title", atom_ns),
            "authors": [text_of(a, "a:name", atom_ns) for a in entry.findall("a:author", atom_ns)],
            "published": text_of(entry, "a:updated", atom_ns) or text_of(entry, "a:published", atom_ns),
            "venue": "",
            "url": text_of(entry, "a:id", atom_ns),
            "abstract": text_of(entry, "a:summary", atom_ns),
        }


def text_of(node: ET.Element, path: str, ns: dict[str, str]) -> str:
    found = node.find(path, ns)
    return normalize_space(found.text if found is not None else "")


def child_text(node: ET.Element, tag: str) -> str:
    found = node.find(tag)
    return normalize_space(found.text if found is not None else "")


def strip_tags(value: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", " ", value or ""))


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1]


def element_text(element: ET.Element) -> str:
    return normalize_space(" ".join(t for t in element.itertext() if normalize_space(t)))


def load_manual_exports(config: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    manual = config.get("manual_exports") or {}
    if not manual.get("enabled", False):
        return []
    formats = {f.lower().lstrip(".") for f in manual.get("formats", ["ris", "bib", "csv", "txt"])}
    items: list[dict[str, Any]] = []
    for path_value in manual.get("paths", []):
        path = resolve_path(path_value, root)
        if not path or not path.exists():
            continue
        for file in path.rglob("*"):
            if not file.is_file() or file.suffix.lower().lstrip(".") not in formats:
                continue
            try:
                items.extend(parse_export_file(file))
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] manual export failed {file}: {exc}", file=sys.stderr)
    return [clean_item({**item, "source": item.get("source") or "manual_exports"}) for item in items]


def parse_export_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".ris":
        return parse_ris(path)
    if suffix == ".bib":
        return parse_bib(path)
    if suffix == ".csv":
        return parse_csv(path)
    if suffix == ".txt":
        return parse_cnki_txt(path)
    return []


def parse_ris(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    authors: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if len(raw) < 6 or raw[2:6] != "  - ":
            continue
        tag, value = raw[:2], normalize_space(raw[6:])
        if tag == "TY":
            current, authors = {}, []
        elif tag in {"TI", "T1"}:
            current["title"] = value
        elif tag == "AU":
            authors.append(value)
        elif tag in {"PY", "Y1"}:
            current["year"] = value[:4]
            current["published"] = value
        elif tag in {"JO", "JF", "T2"}:
            current["venue"] = value
        elif tag == "DO":
            current["doi"] = value
        elif tag in {"UR", "L1"}:
            current["url"] = value
        elif tag == "AB":
            current["abstract"] = value
        elif tag == "ER":
            current["authors"] = authors
            current["origin"] = str(path)
            if current.get("title"):
                records.append(current)
    return records


def parse_bib(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    records = []
    for match in re.finditer(r"@\w+\s*\{[^,]+,(.*?)\n\}", text, flags=re.S):
        body = match.group(1)
        fields = {
            key.lower(): normalize_space(value.strip("{}\""))
            for key, value in re.findall(r"(\w+)\s*=\s*({.*?}|\".*?\")\s*,?", body, flags=re.S)
        }
        records.append(
                {
                    "title": fields.get("title"),
                    "authors": fields.get("author", "").replace(" and ", "; "),
                    "year": fields.get("year"),
                    "venue": fields.get("journal") or fields.get("booktitle"),
                    "doi": fields.get("doi"),
                    "url": fields.get("url"),
                    "abstract": fields.get("abstract"),
                    "keywords": fields.get("keywords") or fields.get("keyword"),
                    "origin": str(path),
                }
            )
    return [r for r in records if r.get("title")]


def parse_csv(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            lower = {str(k).strip().lower(): v for k, v in row.items()}
            title = first(lower, "title", "article title", "document title", "题名", "篇名")
            if not title:
                continue
            records.append(
                {
                    "title": title,
                    "authors": first(lower, "authors", "author", "作者"),
                    "year": first(lower, "year", "publication year", "发表时间", "年份"),
                    "published": first(lower, "date", "publication date", "发表时间"),
                    "venue": first(lower, "source", "publication title", "journal", "来源", "刊名"),
                    "doi": first(lower, "doi"),
                    "url": first(lower, "url", "link", "链接"),
                    "abstract": first(lower, "abstract", "摘要"),
                    "keywords": first(lower, "keywords", "keyword", "author keywords", "index keywords", "关键词", "关键字"),
                    "origin": str(path),
                }
            )
    return records


def parse_cnki_txt(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    chunks = [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]
    records = []
    for chunk in chunks:
        fields = extract_cnki_fields(chunk)
        if fields.get("title"):
            fields["origin"] = str(path)
            records.append(fields)
    if records:
        return records
    title = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return [{"title": title, "abstract": normalize_space(text), "origin": str(path)}] if title else []


def extract_cnki_fields(chunk: str) -> dict[str, Any]:
    mapping = {
        "title": ["题名", "篇名", "Title"],
        "authors": ["作者", "Author"],
        "venue": ["来源", "刊名", "Source"],
        "year": ["发表时间", "年份", "Year"],
        "doi": ["DOI", "doi"],
        "abstract": ["摘要", "Abstract"],
        "keywords": ["关键词", "关键字", "Keywords", "Keyword"],
    }
    result: dict[str, Any] = {}
    for key, names in mapping.items():
        for name in names:
            pattern = rf"{re.escape(name)}\s*[:：]\s*(.+)"
            found = re.search(pattern, chunk)
            if found:
                result[key] = normalize_space(found.group(1))
                break
    if "title" not in result:
        first_line = next((line.strip() for line in chunk.splitlines() if line.strip()), "")
        if first_line and len(first_line) < 240:
            result["title"] = first_line
    return result


def first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and normalize_space(row[name]):
            return row[name]
    return ""


def keyword_score(item: dict[str, Any], config: dict[str, Any]) -> tuple[int, list[str]]:
    text = " ".join(
        normalize_space(item.get(k))
        for k in ("title", "abstract", "keywords", "venue")
    ).lower()
    raw_text = " ".join(normalize_space(item.get(k)) for k in ("title", "abstract", "keywords", "venue"))
    keywords = config.get("keywords") or {}
    scoring = config.get("scoring") or {}
    weights = scoring.get("weights") or {}
    score = 0
    hits: list[str] = []
    excluded = []
    for term in keywords.get("exclude", []):
        if normalize_space(term).lower() in text:
            excluded.append(term)
    if excluded:
        return -99, [f"excluded:{term}" for term in excluded]
    for group, terms in keywords.items():
        if group == "exclude" or not isinstance(terms, list):
            continue
        weight = int(weights.get(group, 1))
        for term in terms:
            needle = normalize_space(term)
            if not needle:
                continue
            haystack = raw_text if contains_cjk(needle) else text
            query = needle if contains_cjk(needle) else needle.lower()
            # 英文缩写和单词必须按词边界匹配，避免 RAG 误命中 stoRAGe、
            # AI 误命中包含相同字母的普通单词；含空格/连字符的短语同样保留边界。
            matched = query in haystack if contains_cjk(needle) else bool(
                re.search(rf"(?<![0-9a-z]){re.escape(query)}(?![0-9a-z])", haystack)
            )
            if matched:
                score += weight
                hits.append(f"{group}:{needle}")
    required_groups = scoring.get("require_any_group") or []
    if required_groups and not any(hit.split(":", 1)[0] in required_groups for hit in hits):
        score -= 3
    return score, hits[:20]


def contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value))


def group_by_language(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按标题是否含中文分成 (英文, 中文) 两组。"""
    en, zh = [], []
    for item in items:
        (zh if contains_cjk(item.get("title")) else en).append(item)
    return en, zh


def apply_language_caps(items: list[dict[str, Any]], target_en: int, target_zh: int) -> list[dict[str, Any]]:
    """宁缺毋滥：英文/中文各自按相关度排序后封顶（数量不足则少），英文在前中文在后。"""
    en_items, zh_items = group_by_language(items)
    en_items.sort(key=lambda i: int(i.get("score", 0)), reverse=True)
    zh_items.sort(key=lambda i: int(i.get("score", 0)), reverse=True)
    return en_items[: max(0, target_en)] + zh_items[: max(0, target_zh)]


def normalize_venue_key(value: Any) -> str:
    text = normalize_space(value).lower()
    text = text.replace("&", "and")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def configured_journal_terms(config: dict[str, Any]) -> list[str]:
    journal_filter = config.get("journal_filter") or {}
    terms: list[str] = []
    terms.extend(journal_filter.get("allowed_venues") or [])
    categories = journal_filter.get("categories") or {}
    if isinstance(categories, dict):
        for values in categories.values():
            if isinstance(values, list):
                terms.extend(values)
            elif isinstance(values, dict):
                terms.extend(values.get("journals") or [])
    return [normalize_space(term) for term in terms if normalize_space(term)]


def journal_article_type_status(item: dict[str, Any]) -> tuple[bool | None, str]:
    """根据多来源类型元数据判断期刊论文；None 表示来源未提供类型。"""
    publication_type = normalize_space(item.get("publication_type"))
    venue_type = normalize_space(item.get("venue_type"))
    type_text = normalize_space(f"{publication_type} {venue_type}").lower()
    if not type_text:
        return None, ""

    compact = re.sub(r"[^a-z0-9]+", "", type_text)
    rejected = (
        "conference",
        "proceedings",
        "bookchapter",
        "booksection",
        "book",
        "preprint",
        "postedcontent",
        "dissertation",
        "thesis",
        "dataset",
        "report",
    )
    if any(marker in compact for marker in rejected):
        return False, type_text

    accepted = ("journalarticle", "journal", "article", "review", "letter", "editorial", "earlyaccess")
    if any(marker in compact for marker in accepted) or compact in {"ar", "re", "le", "ed"}:
        return True, type_text
    return None, type_text


def document_type_category(item: dict[str, Any]) -> str:
    """把不同数据源的文献类型归一化，供独立的预印本/会议开关使用。"""
    publication_type = normalize_space(item.get("publication_type"))
    venue_type = normalize_space(item.get("venue_type"))
    type_text = normalize_space(f"{publication_type} {venue_type}").lower()
    compact = re.sub(r"[^a-z0-9]+", "", type_text)
    if any(marker in compact for marker in ("preprint", "postedcontent", "arxiv")):
        return "preprint"
    if any(marker in compact for marker in ("conference", "proceedings", "proceeding")):
        return "conference"
    status, _ = journal_article_type_status(item)
    if status is True:
        return "journal"
    if status is False:
        return "other"
    return "unknown"


def journal_filter_match(item: dict[str, Any], config: dict[str, Any]) -> tuple[bool, list[str]]:
    journal_filter = config.get("journal_filter") or {}
    if not journal_filter.get("enabled", False):
        return True, []
    if item.get("bypass_journal_whitelist"):
        # 部分数据源（如 NAPSTIC 中文检索）覆盖全网期刊，白名单无意义，由源配置显式跳过。
        return True, ["bypass:journal-whitelist"]

    document_type = document_type_category(item)
    if document_type == "preprint":
        return bool(journal_filter.get("allow_preprints", False)), ["type:preprint"]
    if document_type == "conference":
        return bool(journal_filter.get("allow_conference_papers", False)), ["type:conference"]
    if document_type == "other" and journal_filter.get("journal_articles_only", False):
        return False, []

    venue = normalize_space(item.get("venue"))
    if not venue:
        return bool(journal_filter.get("allow_missing_venue", False)), []

    venue_key = normalize_venue_key(venue)
    for excluded in journal_filter.get("excluded_venues") or []:
        excluded_key = normalize_venue_key(excluded)
        if excluded_key and (excluded_key == venue_key or excluded_key in venue_key or venue_key in excluded_key):
            return False, []

    hits: list[str] = []
    for term in configured_journal_terms(config):
        term_key = normalize_venue_key(term)
        if not term_key:
            continue
        if term_key == venue_key or term_key in venue_key or venue_key in term_key:
            hits.append(term)

    for pattern in journal_filter.get("allowed_venue_patterns") or []:
        if re.search(str(pattern), venue, flags=re.I):
            hits.append(str(pattern))

    if not hits:
        return False, []

    if journal_filter.get("journal_articles_only", False):
        type_status, type_label = journal_article_type_status(item)
        if type_status is False:
            return False, []
        if type_status is None and not journal_filter.get("allow_missing_document_type", True):
            return False, []
        if type_status is True and type_label:
            hits.append(f"type:{type_label}")

    return True, hits[:10]


def should_full_analyze(item: dict[str, Any], config: dict[str, Any]) -> bool:
    if not normalize_space(item.get("abstract")):
        return False
    policy = config.get("output_policy") or {}
    if policy.get("full_analysis_requires_oa", True):
        return item.get("is_oa") is True
    return True


def should_analyze_item(item: dict[str, Any], config: dict[str, Any]) -> bool:
    return normalize_space(item.get("abstract")) != ""


def evidence_level(item: dict[str, Any], config: dict[str, Any]) -> str:
    if not normalize_space(item.get("abstract")):
        return "keywords_only"
    if should_full_analyze(item, config):
        return "oa_full_analysis"
    return "abstract_only"


def is_llm_power_item(item: dict[str, Any]) -> bool:
    return any(str(hit).startswith("llm_power:") for hit in item.get("hits") or [])


def title_similarity(left: Any, right: Any) -> float:
    left_tokens = set(re.findall(r"[0-9a-z\u4e00-\u9fff]+", normalize_space(left).lower()))
    right_tokens = set(re.findall(r"[0-9a-z\u4e00-\u9fff]+", normalize_space(right).lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def merge_openalex_work(item: dict[str, Any], work: dict[str, Any], source: str) -> bool:
    changed = False
    abstract = inverted_abstract(work.get("abstract_inverted_index"))
    if abstract and not normalize_space(item.get("abstract")):
        item["abstract"] = abstract
        item["abstract_source"] = source
        changed = True
    open_access = work.get("open_access") or {}
    if item.get("is_oa") is not True and open_access.get("is_oa") is not None:
        item["is_oa"] = bool(open_access.get("is_oa"))
    if not item.get("oa_url"):
        primary = work.get("primary_location") or {}
        item["oa_url"] = normalize_space(open_access.get("oa_url") or primary.get("pdf_url") or primary.get("landing_page_url"))
    if not item.get("access_status"):
        item["access_status"] = normalize_space(open_access.get("oa_status"))
    if not item.get("keywords"):
        item["keywords"] = split_keywords([c.get("display_name", "") for c in work.get("concepts", []) if c.get("display_name")])
    return changed


def enrich_from_openalex(item: dict[str, Any], timeout: int, min_similarity: float) -> bool:
    doi = normalize_doi(item.get("doi"))
    if doi:
        params = {"filter": f"doi:{doi}", "per-page": "1"}
        data = http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
        results = data.get("results") or []
        if results:
            return merge_openalex_work(item, results[0], "openalex_doi")
    title = normalize_space(item.get("title"))
    if not title:
        return False
    params = {"search": title, "per-page": "3"}
    data = http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
    for work in data.get("results") or []:
        if title_similarity(title, work.get("title")) >= min_similarity:
            return merge_openalex_work(item, work, "openalex_title")
    return False


def enrich_from_semantic_scholar(item: dict[str, Any], api_key: str, timeout: int, min_similarity: float, min_interval: float) -> bool:
    title = normalize_space(item.get("title"))
    if not title:
        return False
    params = {
        "query": title,
        "limit": "3",
        "fields": "title,abstract,openAccessPdf,url,fieldsOfStudy,s2FieldsOfStudy",
    }
    data = semantic_scholar_json(
        "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params),
        api_key=api_key,
        timeout=timeout,
        min_interval=min_interval,
    )
    for paper in data.get("data") or []:
        if title_similarity(title, paper.get("title")) < min_similarity:
            continue
        abstract = normalize_space(paper.get("abstract"))
        if abstract and not normalize_space(item.get("abstract")):
            item["abstract"] = abstract
            item["abstract_source"] = "semantic_scholar_title"
            oa_pdf = paper.get("openAccessPdf") or {}
            if oa_pdf.get("url"):
                item["is_oa"] = True
                item["oa_url"] = normalize_space(oa_pdf.get("url"))
                item["access_status"] = "open"
            if not item.get("keywords"):
                s2_fields = [f.get("category", "") for f in paper.get("s2FieldsOfStudy") or [] if f.get("category")]
                item["keywords"] = split_keywords((paper.get("fieldsOfStudy") or []) + s2_fields)
            return True
    return False


def enrich_missing_abstracts(items: list[dict[str, Any]], config: dict[str, Any]) -> None:
    enrichment = config.get("abstract_enrichment") or {}
    if not enrichment.get("enabled", True):
        return
    max_items = int(enrichment.get("max_items", 40))
    timeout = int(enrichment.get("timeout_seconds", 20))
    min_similarity = float(enrichment.get("title_min_similarity", 0.72))
    semantic_interval = float(enrichment.get("semantic_scholar_min_interval_seconds", 1.2))
    semantic_key = env_value(enrichment.get("semantic_scholar_api_key_env", "SEMANTIC_SCHOLAR_API_KEY"))
    semantic_enabled = bool(enrichment.get("semantic_scholar_if_key", True) and semantic_key)
    elsevier_key = env_value(enrichment.get("elsevier_api_key_env", "ELSEVIER_API_KEY"))
    elsevier_enabled = bool(enrichment.get("elsevier_if_key", True) and elsevier_key)
    attempted = 0
    filled = 0
    for item in items:
        if normalize_space(item.get("abstract")):
            continue
        if attempted >= max_items:
            break
        attempted += 1
        try:
            if enrich_from_openalex(item, timeout, min_similarity):
                filled += 1
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] abstract enrichment OpenAlex failed: {safe_error(exc)}", file=sys.stderr)
        if elsevier_enabled:
            try:
                abstract = fetch_elsevier_article_abstract(elsevier_key, item, timeout=timeout)
                if abstract:
                    item["abstract"] = abstract
                    item["abstract_source"] = "elsevier_article_xml"
                    filled += 1
                    continue
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    print("[warn] abstract enrichment Elsevier rate limited; skipping remaining Elsevier enrichment", file=sys.stderr)
                    elsevier_enabled = False
                elif exc.code in {401, 403}:
                    print("[warn] abstract enrichment Elsevier article retrieval is not authorized; using other abstract sources", file=sys.stderr)
                    elsevier_enabled = False
                else:
                    print(f"[warn] abstract enrichment Elsevier failed: {safe_error(exc)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] abstract enrichment Elsevier failed: {safe_error(exc)}", file=sys.stderr)
        if semantic_enabled:
            try:
                if enrich_from_semantic_scholar(item, semantic_key, timeout, min_similarity, semantic_interval):
                    filled += 1
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    print("[warn] abstract enrichment Semantic Scholar rate limited; skipping remaining Semantic Scholar enrichment", file=sys.stderr)
                    semantic_enabled = False
                else:
                    print(f"[warn] abstract enrichment Semantic Scholar failed: {safe_error(exc)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"[warn] abstract enrichment Semantic Scholar failed: {safe_error(exc)}", file=sys.stderr)
    if attempted:
        print(f"[info] abstract enrichment: filled {filled}/{attempted} missing abstracts")


def dedupe_and_score(items: list[dict[str, Any]], config: dict[str, Any], seen: set[str]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    min_score = int((config.get("scoring") or {}).get("min_score", 4))
    for item in items:
        if not item.get("title"):
            continue
        journal_ok, journal_hits = journal_filter_match(item, config)
        if not journal_ok:
            continue
        score, hits = keyword_score(item, config)
        stable_key = item_key(item)
        key = normalized_title_key(item) or stable_key
        item["score"] = score
        item["hits"] = hits
        item["journal_filter_hits"] = journal_hits
        item["key"] = stable_key
        item["dedupe_key"] = key
        item["seen_before"] = key in seen or stable_key in seen
        src = str(item.get("source", ""))
        if score < min_score and not (src.startswith(("manual", "napstic")) or "napstic" in src.lower()):
            continue
        if key not in by_key or score > int(by_key[key].get("score", 0)):
            by_key[key] = item
    # 先按发布日期倒序，再用稳定排序把相关性分数作为第一优先级。
    ranked = sorted(by_key.values(), key=lambda x: x.get("published", ""), reverse=True)
    ranked.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
    # 状态文件记录的是已经成功推送的文献。历史命中的记录必须在生成报告前剔除，
    # 否则 seen_before 仅用于排序，仍会被再次写入邮件与附件。
    return [item for item in ranked if not item.get("seen_before", False)]


def read_state(path: Path | None) -> set[str]:
    if not path or not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("seen", []))


def write_state(path: Path | None, keys: Iterable[str]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_state(path)
    existing.update(keys)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp_path.write_text(json.dumps({"seen": sorted(existing)}, ensure_ascii=False, indent=2), encoding="utf-8")
        # os.replace 在同一文件系统内为原子替换，避免任务中断留下半截 JSON。
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def run_lock(path: Path | None, stale_seconds: int = 7200) -> Iterator[None]:
    """阻止同一配置被定时任务和手动命令并发执行。"""
    if path is None:
        yield
        return

    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists() and time.time() - lock_path.stat().st_mtime > stale_seconds:
        lock_path.unlink()

    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Radar is already running for this state file: {path}") from exc

    try:
        os.write(descriptor, f"pid={os.getpid()} started={dt.datetime.now().isoformat()}\n".encode("utf-8"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def write_outputs(items: list[dict[str, Any]], config: dict[str, Any], root: Path) -> tuple[Path, Path, Path, Path]:
    profile = config.get("profile") or {}
    output_dir = resolve_path(profile.get("output_dir", "outputs/power-system-radar"), root)
    assert output_dir is not None
    output_dir.mkdir(parents=True, exist_ok=True)
    enrich_missing_abstracts(items, config)
    enrich_interpretations(items, config)
    daily_brief = build_daily_brief(items, config)
    # 微秒级时间戳避免两个计划任务同时写出时互相覆盖。
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = output_dir / f"records_{stamp}.json"
    md_path = output_dir / f"digest_{stamp}.md"
    html_path = output_dir / f"digest_{stamp}.html"
    dash_path = output_dir / f"dashboard_{stamp}.html"
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_digest_markdown(items, config), encoding="utf-8")
    html_path.write_text(render_digest_html(items, config, daily_brief), encoding="utf-8")
    dash_path.write_text(render_dashboard_html(items, config), encoding="utf-8")
    return md_path, json_path, html_path, dash_path


def enrich_interpretations(items: list[dict[str, Any]], config: dict[str, Any]) -> None:
    interpretation = config.get("interpretation") or {}
    if not interpretation.get("enabled", True):
        return

    configured_max = int(interpretation.get("max_items", 0))
    max_interpreted = len(items) if configured_max <= 0 else configured_max
    analysis_items = [item for item in items if should_analyze_item(item, config)][:max_interpreted]
    for item in items:
        if not should_analyze_item(item, config):
            item["interpretation_mode"] = "keywords_only"
        abstract = normalize_space(item.get("abstract"))
        if abstract and contains_cjk(abstract):
            item["abstract_zh"] = abstract

    for item in analysis_items:
        item["interpretation"] = interpret_item(item, config)
        item["interpretation_mode"] = "rule"

    llm_config = config.get("llm_interpretation") or {}
    if not llm_config.get("enabled", False):
        return

    api_key = env_value(llm_config.get("api_key_env", "DEEPSEEK_API_KEY"))
    if not api_key:
        print("[warn] llm_interpretation enabled but DEEPSEEK_API_KEY is missing; using rule-based interpretation", file=sys.stderr)
        return

    configured_llm_max = int(llm_config.get("max_items", 0))
    max_llm = len(analysis_items) if configured_llm_max <= 0 else min(configured_llm_max, len(analysis_items))
    for item in analysis_items[:max_llm]:
        if contains_cjk(item.get("title")):
            # 中文文献本就提供中文摘要与规则解读，无需 LLM 翻译；跳过以节省配额
            continue
        fallback = item.get("interpretation") or interpret_item(item, config)
        try:
            deepseek_result = interpret_item_with_deepseek_retry(item, llm_config, fallback, api_key)
            item["interpretation"] = deepseek_result
            item["abstract_zh"] = normalize_space(deepseek_result.get("abstract_zh")) or normalize_space(item.get("abstract_zh"))
            item["interpretation_mode"] = "deepseek"
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] deepseek interpretation failed for one item: {safe_error(exc)}; using rule-based interpretation", file=sys.stderr)
            item["interpretation"] = fallback
            item["interpretation_mode"] = "rule_fallback"


def interpret_item_with_deepseek_retry(
    item: dict[str, Any],
    llm_config: dict[str, Any],
    fallback: dict[str, str],
    api_key: str,
) -> dict[str, str]:
    """重试瞬时失败，并将英文摘要缺少中文译文视为不完整响应。"""
    attempts = max(1, int(llm_config.get("attempts", 2)))
    delay = max(0.0, float(llm_config.get("retry_delay_seconds", 1.0)))
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            result = interpret_item_with_deepseek(item, llm_config, fallback, api_key)
            abstract = normalize_space(item.get("abstract"))
            if abstract and not contains_cjk(abstract) and not normalize_space(result.get("abstract_zh")):
                raise ValueError("DeepSeek response omitted abstract_zh")
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def llm_endpoint(llm_config: dict[str, Any]) -> str:
    """LLM 端点：优先 DEEPSEEK_BASE_URL 环境变量（本地可覆盖为任意 OpenAI 兼容网关），否则用配置值。"""
    default = str(llm_config.get("base_url", "https://api.deepseek.com/chat/completions"))
    return env_value(llm_config.get("base_url_env", "DEEPSEEK_BASE_URL"), default)


def llm_headers(api_key: str) -> dict[str, str]:
    """部分 OpenAI 兼容网关用浏览器 UA 才放行（如 opencode.ai 的 Cloudflare 校验）。"""
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }


def llm_model(llm_config: dict[str, Any]) -> str:
    """LLM 模型名：优先 DEEPSEEK_MODEL 环境变量（如免费档 deepseek-v4-flash-free），否则用配置值。"""
    default = str(llm_config.get("model", "deepseek-v4-flash"))
    return env_value(llm_config.get("model_env", "DEEPSEEK_MODEL"), default)


def interpret_item_with_deepseek(
    item: dict[str, Any],
    llm_config: dict[str, Any],
    fallback: dict[str, str],
    api_key: str,
) -> dict[str, str]:
    model = llm_model(llm_config)
    timeout = int(llm_config.get("timeout_seconds", 60))
    payload = {
        "model": model,
        "temperature": float(llm_config.get("temperature", 0.2)),
        "max_tokens": int(llm_config.get("max_tokens", 1200)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是电力系统优化与综合能源系统优化方向的中文文献助理。"
                    "只依据用户提供的题名、摘要、来源、OA状态和关键词命中进行判断，不能编造论文未给出的实验结果、公式或结论。"
                    "如果没有可靠OA全文链接，你的分析必须明确是基于摘要和元数据，不要写成全文精读。"
                    "请输出严格 JSON，字段必须为 abstract_zh, problem, method, innovation, application, value, caveat。"
                    "abstract_zh 必须是英文摘要的忠实、完整中文翻译，不是摘要概括；保留术语、缩写、数值和逻辑关系。"
                    "每个字段用中文写 1-3 句，innovation 要尽量解释可能的新意来自哪里，caveat 要说明需要阅读全文核实的点。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": item.get("title"),
                        "authors": item.get("authors"),
                        "year": item.get("year"),
                        "venue": item.get("venue"),
                        "source": item.get("source"),
                        "doi": item.get("doi"),
                        "url": item.get("url"),
                        "is_oa": item.get("is_oa"),
                        "oa_url": item.get("oa_url"),
                        "score": item.get("score"),
                        "keyword_hits": item.get("hits"),
                        "abstract": textwrap.shorten(normalize_space(item.get("abstract")), width=6000, placeholder=" ..."),
                        "rule_based_fallback": fallback,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    }
    data = http_post_json(
        llm_endpoint(llm_config),
        payload,
        llm_headers(api_key),
        timeout=timeout,
    )
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = parse_json_object(content)
    return normalize_interpretation(parsed, fallback)


def parse_json_object(content: str) -> dict[str, Any]:
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM response is not a JSON object")
    return data


def normalize_interpretation(data: dict[str, Any], fallback: dict[str, str]) -> dict[str, str]:
    fields = ("abstract_zh", "problem", "method", "innovation", "application", "value", "caveat")
    result: dict[str, str] = {}
    for field in fields:
        value = normalize_space(data.get(field))
        result[field] = value if value else fallback.get(field, "")
    return result


def build_daily_brief(items: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """汇总本次未推送候选；DeepSeek 只写内容，HTML 仍由本地模板生成。"""
    fallback = fallback_daily_brief(items)
    if not items:
        return fallback
    llm_config = config.get("llm_interpretation") or {}
    if not llm_config.get("enabled", False):
        return fallback
    api_key = env_value(llm_config.get("api_key_env", "DEEPSEEK_API_KEY"))
    if not api_key:
        return fallback

    papers = []
    for item in items:
        analysis = item.get("interpretation") or {}
        papers.append({
            "title": item.get("title"),
            "venue": item.get("venue"),
            "year": item.get("year"),
            "score": item.get("score"),
            "is_oa": item.get("is_oa"),
            "keyword_hits": item.get("hits"),
            "problem": analysis.get("problem"),
            "method": analysis.get("method"),
            "innovation": analysis.get("innovation"),
            "value": analysis.get("value"),
        })
    payload = {
        "model": llm_model(llm_config),
        "temperature": float(llm_config.get("temperature", 0.2)),
        "max_tokens": min(int(llm_config.get("max_tokens", 1200)), 1200),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是电力系统领域的中文文献情报编辑。请把本次筛选出的全部未推送文献综合成一份总体简报。"
                    "系统优先选择最近14天的新文献，数量不足时会从较早但从未推送的相关文献中补位；"
                    "不要逐篇列标题、作者或文献信息，不要编造摘要之外的结论。输出严格 JSON："
                    "overview 为2-3句总体概述；trends 为3-5条研究趋势字符串数组；"
                    "method_signal 为1-2句方法信号；takeaway 为1-2句今日阅读建议。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"paper_count": len(items), "selection_policy": "近期新增优先，不足时用从未推送文献补位", "papers": papers},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    try:
        data = http_post_json(
            llm_endpoint(llm_config),
            payload,
            llm_headers(api_key),
            timeout=int(llm_config.get("timeout_seconds", 60)),
        )
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = parse_json_object(content)
        trends = parsed.get("trends") if isinstance(parsed.get("trends"), list) else []
        return {
            "overview": normalize_space(parsed.get("overview")) or fallback["overview"],
            "trends": [normalize_space(value) for value in trends if normalize_space(value)][:5] or fallback["trends"],
            "method_signal": normalize_space(parsed.get("method_signal")) or fallback["method_signal"],
            "takeaway": normalize_space(parsed.get("takeaway")) or fallback["takeaway"],
            "source": "deepseek",
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] deepseek daily brief failed: {safe_error(exc)}; using deterministic fallback", file=sys.stderr)
        return fallback


def fallback_daily_brief(items: list[dict[str, Any]]) -> dict[str, Any]:
    keyword_map = build_keyword_paper_map(items)
    top_keywords = sorted(keyword_map.items(), key=lambda pair: (-len(pair[1]), pair[0].lower()))[:5]
    high = sum(1 for item in items if int(item.get("score", 0)) >= 7)
    oa = sum(1 for item in items if item.get("is_oa") is True)
    trends = [f"{keyword}：{len(papers)} 篇" for keyword, papers in top_keywords]
    return {
        "overview": f"今日筛选出 {len(items)} 篇尚未推送的相关文献，其中高相关 {high} 篇、开放获取 {oa} 篇。",
        "trends": trends or ["今日暂无可归纳的关键词趋势。"],
        "method_signal": "方法信号基于关键词与已有逐篇解读汇总，详细内容请查看附件。",
        "takeaway": "建议优先查看高相关和开放获取文献，并在正式引用前核对原文。",
        "source": "fallback",
    }


def safe_error(exc: Exception) -> str:
    message = normalize_space(str(exc))
    message = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-***", message)
    return message[:240]


def render_markdown(items: list[dict[str, Any]], config: dict[str, Any]) -> str:
    title = (config.get("profile") or {}).get("name", "电力系统文献雷达")
    interpretation = config.get("interpretation") or {}
    configured_max = int(interpretation.get("max_items", 0))
    max_interpreted = len(items) if configured_max <= 0 else configured_max
    lines = [
        f"# {title}：中文文献解读",
        "",
        f"- 生成时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        f"- 本次记录数：{len(items)}",
        "- 解读说明：以下“创新点/价值”基于题名、摘要、来源元数据和关键词命中自动推断，正式引用前请阅读全文核实。",
        "",
    ]
    if items:
        lines.extend(["## 优先阅读建议", ""])
        for item in items[: min(5, len(items))]:
            lines.append(
                f"- **{item.get('title')}**：相关度 {item.get('score')}，建议关注 {', '.join(infer_focus_terms(item)[:3]) or '模型与算例'}。"
            )
        lines.append("")
    for i, item in enumerate(items, 1):
        authors = ", ".join(item.get("authors") or [])
        meta = " | ".join(p for p in [item.get("venue"), str(item.get("year") or ""), item.get("source")] if p)
        analysis = item.get("interpretation")
        oa_label = "OA" if item.get("is_oa") is True else "non-OA/unknown"
        oa_url = item.get("oa_url") or ""
        journal_hits = ", ".join(item.get("journal_filter_hits") or [])
        access_lines = [
            f"- Journal filter: {journal_hits}",
            f"- OA status: {oa_label}" + (f" | OA URL: {oa_url}" if oa_url else ""),
        ]
        lines.extend(
            [
                f"## {i}. {item.get('title')}",
                "",
                f"- 相关度评分：{item.get('score')}（{', '.join(item.get('hits') or [])}）",
                f"- 来源信息：{meta}",
                f"- 作者：{authors}" if authors else "- 作者：",
                f"- DOI: {item.get('doi') or ''}",
                f"- 链接：{item.get('url') or ''}",
                f"- 是否已见过：{item.get('seen_before')}",
                "",
            ]
        )
        lines.extend(access_lines + [""])
        if i <= max_interpreted and should_full_analyze(item, config) and analysis:
            lines.extend(
                [
                    "### 中文解读",
                    "",
                    f"- **研究问题**：{analysis['problem']}",
                    f"- **方法路线**：{analysis['method']}",
                    f"- **可能创新点**：{analysis['innovation']}",
                    f"- **应用场景**：{analysis['application']}",
                    f"- **对你课题的借鉴价值**：{analysis['value']}",
                    f"- **需要阅读全文核实**：{analysis['caveat']}",
                    "",
                ]
            )
        elif not should_full_analyze(item, config):
            lines.extend(
                [
                    "### Abstract-only note",
                    "",
                    "No reliable OA full-text URL was found, so this item is limited to title/source/abstract and is not treated as full-text analysis.",
                    "",
                ]
            )
        abstract = normalize_space(item.get("abstract"))
        abstract_zh = normalize_space(item.get("abstract_zh"))
        is_zh = contains_cjk(item.get("title"))
        if abstract:
            lines.extend(
                [
                    f"### {'中文摘要（原文）' if is_zh else '英文摘要（原文）'}",
                    "",
                    textwrap.shorten(abstract, width=900, placeholder=" ..."),
                    "",
                ]
            )
            if not is_zh and abstract_zh:
                lines.extend(["### 中文翻译", "", textwrap.shorten(abstract_zh, width=1200, placeholder=" ..."), ""])
    return "\n".join(lines)


def render_digest_markdown(items: list[dict[str, Any]], config: dict[str, Any]) -> str:
    title = (config.get("profile") or {}).get("name", "power-system-radar")
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    oa_count = sum(1 for item in items if evidence_level(item, config) == "oa_full_analysis")
    abstract_count = sum(1 for item in items if evidence_level(item, config) == "abstract_only")
    keyword_count = sum(1 for item in items if evidence_level(item, config) == "keywords_only")
    lines = [
        f"# {title}：高水平期刊文献雷达",
        "",
        f"- 生成时间：{generated_at}",
        f"- 本次记录：{len(items)} 篇",
        f"- 输出分层：OA整体分析 {oa_count} 篇；非OA摘要 {abstract_count} 篇；无摘要仅题名/关键词初筛 {keyword_count} 篇",
        "- 说明：仅命中配置白名单期刊；非OA或无摘要记录不做全文级创新点判断。",
        "",
    ]
    # 中英分区：英文在前、中文在后，各自带计数标题
    en_group, zh_group = group_by_language(items)
    section_counts = {False: len(en_group), True: len(zh_group)}
    last_is_zh: bool | None = None
    for i, item in enumerate(en_group + zh_group, 1):
        is_zh = contains_cjk(item.get("title"))
        if is_zh != last_is_zh:
            label = "中文文献" if is_zh else "英文文献"
            lines.extend([f"## {label}（{section_counts[is_zh]} 篇）", ""])
            last_is_zh = is_zh
        analysis = item.get("interpretation")
        authors = ", ".join(item.get("authors") or [])
        keywords = ", ".join(item.get("keywords") or [])
        hits = ", ".join(item.get("hits") or [])
        journal_hits = ", ".join(item.get("journal_filter_hits") or [])
        level = evidence_level(item, config)
        level_label = {
            "oa_full_analysis": "OA/开放版本：输出整体分析",
            "abstract_only": "非OA或OA未知：只输出摘要",
            "keywords_only": "无摘要：仅题名/关键词初筛",
        }.get(level, level)
        focus_label = "LLM+电力系统重点关注" if is_llm_power_item(item) else "常规电力系统优化/调度"
        lines.extend(
            [
                f"## {i}. {item.get('title')}",
                "",
                "### 1. 基本信息",
                "",
                f"- 期刊/来源：{item.get('venue') or item.get('source') or ''}",
                f"- 年份：{item.get('year') or ''}",
                f"- 作者：{authors}",
                f"- DOI：{item.get('doi') or ''}",
                f"- 链接：{item.get('url') or ''}",
                f"- OA链接：{item.get('oa_url') or ''}",
                "",
                "### 2. 筛选判断",
                "",
                f"- 输出类型：{level_label}",
                f"- 重点方向：{focus_label}",
                f"- 期刊命中：{journal_hits}",
                f"- 关键词命中：{hits}",
                f"- 相关度评分：{item.get('score')}",
                "",
            ]
        )
        if item.get("title_en"):
            lines.extend([f"- 英文题名：{item.get('title_en')}", ""])
        if level in {"oa_full_analysis", "abstract_only"} and analysis:
            analysis_title = "### 3. OA开放版本整体分析" if level == "oa_full_analysis" else "### 3. 基于摘要的中文分析"
            basis_note = "以下分析基于开放版本/摘要与元数据。" if level == "oa_full_analysis" else "以下分析仅基于摘要与元数据，不等同于全文精读。"
            lines.extend(
                [
                    analysis_title,
                    "",
                    basis_note,
                    "",
                    f"- 研究问题：{analysis['problem']}",
                    f"- 方法路线：{analysis['method']}",
                    f"- 可能创新点：{analysis['innovation']}",
                    f"- 应用场景：{analysis['application']}",
                    f"- 对课题借鉴价值：{analysis['value']}",
                    f"- 需要核实：{analysis['caveat']}",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "### 3. 仅题名/关键词初筛",
                    "",
                    "当前记录没有摘要，相关性只来自题名、期刊和关键词命中；建议手动点开来源核实后再决定是否阅读。",
                    "",
                ]
            )
        abstract = normalize_space(item.get("abstract"))
        abstract_zh = normalize_space(item.get("abstract_zh"))
        is_zh = contains_cjk(item.get("title"))
        lines.extend(["### 4. 原始信息", ""])
        if abstract:
            if item.get("abstract_source"):
                lines.extend([f"**摘要来源：** {item.get('abstract_source')}", ""])
            if is_zh:
                lines.extend(["**中文摘要：**", "", textwrap.shorten(abstract, width=1200, placeholder=" ..."), ""])
            else:
                lines.extend(["**英文摘要：**", "", textwrap.shorten(abstract, width=1200, placeholder=" ..."), ""])
                if abstract_zh:
                    lines.extend(["**中文翻译：**", "", textwrap.shorten(abstract_zh, width=1600, placeholder=" ..."), ""])
                else:
                    lines.extend(["**中文翻译：** 暂不可用", ""])
        else:
            lines.extend(["**摘要：** 无", ""])
        lines.extend([f"**关键词：** {keywords or '无'}", ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML digest renderer — CLEAN EMAIL (concise, scannable, no fake navigation)
# ---------------------------------------------------------------------------

def render_digest_html(
    items: list[dict[str, Any]],
    config: dict[str, Any],
    daily_brief: dict[str, Any] | None = None,
) -> str:
    title = (config.get("profile") or {}).get("name", "power-system-radar")
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    oa_count = sum(1 for item in items if evidence_level(item, config) == "oa_full_analysis")
    scores = [int(item.get("score", 0)) for item in items if item.get("score")]
    score_max = max(scores) if scores else 0
    high = sum(1 for s in scores if s >= 7)

    parts: list[str] = []
    parts.append(f"""\
<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f6fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#1e1e2e;line-height:1.5">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:0 8px 24px">
<table role="presentation" width="820" cellpadding="0" cellspacing="0" style="width:100%;max-width:820px">
<tr><td>

<table width="100%" cellpadding="0" cellspacing="0" style="background:linear-gradient(135deg,#0f3460,#16213e);border-radius:0 0 16px 16px;margin-bottom:12px">
<tr><td style="padding:24px 20px;text-align:center">
  <div style="font-size:24px;font-weight:800;color:#e94560">⚡ 电力系统文献雷达</div>
  <div style="font-size:13px;color:#8892b0;margin-top:2px">{generated_at}</div>
</td></tr></table>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;margin-bottom:16px">
<tr>
  <td style="padding:14px 10px;text-align:center"><div style="font-size:28px;font-weight:800;color:#e94560">{len(items)}</div><div style="font-size:11px;color:#888">📄 文献</div></td>
  <td style="padding:14px 10px;text-align:center"><div style="font-size:28px;font-weight:800;color:#0f3460">{score_max}</div><div style="font-size:11px;color:#888">🎯 最高分</div></td>
  <td style="padding:14px 10px;text-align:center"><div style="font-size:28px;font-weight:800;color:#e94560">{high}</div><div style="font-size:11px;color:#888">🔥 高相关</div></td>
  <td style="padding:14px 10px;text-align:center"><div style="font-size:28px;font-weight:800;color:#0f3460">{oa_count}</div><div style="font-size:11px;color:#888">📖 OA</div></td>
</tr></table>
""")

    brief = daily_brief or fallback_daily_brief(items)
    source_label = "DeepSeek 今日综合" if brief.get("source") == "deepseek" else "本地规则回退"
    trends_html = "".join(
        f'<li style="margin:7px 0;color:#3f4f63;font-size:14px;line-height:1.7">{html_escape(trend)}</li>'
        for trend in brief.get("trends") or []
    )
    parts.append(f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:10px;border:1px solid #e3e9f0;margin:14px 0">
<tr><td style="padding:20px 22px">
  <div style="font-size:19px;font-weight:800;color:#12365a;margin-bottom:10px">今日文献总体简报</div>
  <div style="font-size:15px;line-height:1.8;color:#334155">{html_escape(brief.get('overview') or '')}</div>
  <div style="font-size:15px;font-weight:750;color:#12365a;margin-top:17px">主要研究趋势</div>
  <ul style="margin:5px 0 0;padding-left:22px">{trends_html}</ul>
  <div style="font-size:15px;font-weight:750;color:#12365a;margin-top:17px">方法信号</div>
  <div style="font-size:14px;line-height:1.75;color:#3f4f63;margin-top:5px">{html_escape(brief.get('method_signal') or '')}</div>
  <div style="font-size:15px;font-weight:750;color:#12365a;margin-top:17px">今日阅读建议</div>
  <div style="font-size:14px;line-height:1.75;color:#3f4f63;margin-top:5px">{html_escape(brief.get('takeaway') or '')}</div>
  <div style="font-size:11px;color:#718096;margin-top:16px">简报来源：{html_escape(source_label)}</div>
</td></tr></table>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#eef5f8;border-radius:9px;margin:10px 0">
<tr><td style="padding:14px 18px;font-size:14px;line-height:1.7;color:#294b5f">
  完整的逐篇文献信息、摘要、DeepSeek 解读、关键词分组和交互筛选，均在附件 <b>dashboard HTML</b> 中。
</td></tr></table>""")

    parts.append(f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:#16213e;border-radius:10px;margin-top:16px">
<tr><td style="padding:14px 20px;text-align:center;font-size:11px;color:#8892b0">
  📎 完整摘要、完整解读和关键词交互筛选均在附件 dashboard HTML 中<br>
  邮件正文仅作今日文献速览，引用前请查阅原文
</td></tr></table>
</td></tr></table></td></tr></table></body></html>""")
    return "\n".join(parts)


def _email_card(i: int, item: dict[str, Any]) -> str:
    score = int(item.get("score", 0))
    title = normalize_space(item.get("title") or "(无标题)")
    url = safe_http_url(item.get("url"))
    authors = ", ".join(item.get("authors") or [])[:120]
    venue = item.get("venue") or ""
    year = item.get("year") or ""
    is_oa = item.get("is_oa") is True
    oa_url = safe_http_url(item.get("oa_url"))
    analysis = item.get("interpretation") or {}
    abstract = normalize_space(item.get("abstract"))
    problem = analysis.get("problem", "") if analysis else ""
    method = analysis.get("method", "") if analysis else ""
    innovation = analysis.get("innovation", "") if analysis else ""
    # pick 3 keyword tagsss
    kw_tags = []
    for hit in (item.get("hits") or []):
        t = hit.split(":", 1)[-1].strip() if ":" in hit else hit.strip()
        if t and t not in kw_tags:
            kw_tags.append(t)
    kw_tags = kw_tags[:4]

    sc = "#e94560" if score >= 7 else ("#f0a500" if score >= 4 else "#7b8496")
    bg = "#fde8ec" if score >= 7 else ("#fef6e4" if score >= 4 else "#f2f2f2")
    emoji = "🔥" if score >= 7 else ("⭐" if score >= 4 else "📎")

    title_linked = f'<a href="{html_escape(url)}" target="_blank" style="color:#0f3460;text-decoration:none;font-weight:700">{html_escape(title)}</a>' if url else html_escape(title)
    oa_badge = ' <span style="background:#d4edda;color:#155724;border-radius:3px;padding:1px 6px;font-size:10px;font-weight:600">OA</span>' if is_oa else ''
    kw_html = " ".join(
        f'<span style="display:inline-block;background:#e8ecf1;color:#46576a;border-radius:9px;padding:2px 8px;font-size:11px;margin:1px 2px">{html_escape(k[:24])}</span>'
        for k in kw_tags
    )

    source_button = f'<a href="{html_escape(url)}" target="_blank" style="display:inline-block;background:#0f3460;color:#fff;text-decoration:none;border-radius:7px;padding:9px 15px;font-size:13px;font-weight:700;margin-right:7px">阅读原文 →</a>' if url else ''
    oa_button = f'<a href="{html_escape(oa_url)}" target="_blank" style="display:inline-block;background:#e8f5ee;color:#176b45;text-decoration:none;border-radius:7px;padding:9px 15px;font-size:13px;font-weight:700">OA 全文</a>' if oa_url else ''
    mode = item.get("interpretation_mode") or "keywords_only"
    mode_label = "DeepSeek 解读" if mode == "deepseek" else ("规则回退" if mode == "rule_fallback" else "关键词初筛")
    mode_color = "#176b45" if mode == "deepseek" else "#8a5a00"
    if problem or method or innovation:
        brief_rows = [
            ("研究焦点", textwrap.shorten(problem, width=150, placeholder="…")),
            ("方法与贡献", textwrap.shorten(" ".join(part for part in (method, innovation) if part), width=240, placeholder="…")),
            ("借鉴价值", textwrap.shorten(analysis.get("value", ""), width=150, placeholder="…")),
        ]
    else:
        brief_rows = [("初筛说明", textwrap.shorten(abstract, width=240, placeholder="…") if abstract else "暂无摘要，请打开原文核实。")]
    brief_html = "".join(
        f'<div style="font-size:14px;line-height:1.7;color:#3f4f63;margin:4px 0"><b style="color:#12365a">{html_escape(label)}：</b>{html_escape(content or "待核实")}</div>'
        for label, content in brief_rows
    )

    return f"""\
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;margin-bottom:8px;border-left:3px solid {sc};box-shadow:0 1px 3px rgba(0,0,0,.04)">
<tr><td style="padding:10px 14px">
  <span style="background:{bg};color:{sc};border-radius:4px;padding:1px 8px;font-size:11px;font-weight:700;margin-right:6px">#{i} {emoji} {score}</span>{oa_badge}
</td></tr>
<tr><td style="padding:0 14px 5px"><div style="font-size:17px;font-weight:700;line-height:1.45">{title_linked}</div></td></tr>
<tr><td style="padding:0 14px 4px"><div style="font-size:13px;color:#687386">{html_escape(authors)}</div></td></tr>
<tr><td style="padding:0 14px 5px"><div style="font-size:12px;color:#7b8796">{html_escape(venue)}{" · " + str(year) if year else ""}{" · " + '<a href="' + html_escape(oa_url) + '" target="_blank" style="color:#0f3460;font-size:12px">全文</a>' if oa_url else ""}</div></td></tr>
<tr><td style="padding:4px 14px 6px">{kw_html}</td></tr>
<tr><td style="padding:5px 14px 9px"><div style="background:#f8fafc;border-left:3px solid #8db4c7;padding:9px 11px">{brief_html}<div style="font-size:11px;color:{mode_color};margin-top:5px">内容来源：{html_escape(mode_label)}</div></div></td></tr>
<tr><td style="padding:2px 14px 12px">{source_button}{oa_button}</td></tr>
</table>"""


# ---------------------------------------------------------------------------
# INTERACTIVE DASHBOARD — standalone HTML with JS filtering/search/expand
# ---------------------------------------------------------------------------

def render_dashboard_html(items: list[dict[str, Any]], config: dict[str, Any]) -> str:
    title = (config.get("profile") or {}).get("name", "power-system-radar")
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    oa_count = sum(1 for item in items if evidence_level(item, config) == "oa_full_analysis")
    scores = [int(item.get("score", 0)) for item in items if item.get("score")]
    score_max = max(scores) if scores else 0
    high = sum(1 for s in scores if s >= 7)

    # Language split for the one-page two-section dashboard
    en_group, zh_group = group_by_language(items)
    en_count = len(en_group)
    zh_count = len(zh_group)

    # Keyword mapping for filter chips
    kw_to_papers = build_keyword_paper_map(items)
    sorted_kws = sorted(kw_to_papers.items(), key=lambda x: (-len(x[1]), x[0].lower()))[:25]

    # Serialize paper data as JSON for JS
    papers_json = _papers_json(items)

    return f"""\
<!DOCTYPE html><html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>电力系统文献雷达 — {generated_at}</title>
<style>
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#f6f8fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;line-height:1.55}}
.top{{background:#fff;border-bottom:1px solid #e6ebf2;position:sticky;top:0;z-index:20}}.top-inner{{max-width:1040px;margin:auto;padding:16px 20px 13px}}h1{{font-size:21px;margin:0;color:#12365a}}.meta{{color:#718096;font-size:12px;margin-top:2px}}
.summary{{display:flex;gap:18px;flex-wrap:wrap;margin-top:9px;font-size:12px;color:#526173}}.summary b{{font-size:16px;color:#12365a;margin-right:3px}}
.search-row{{display:flex;gap:10px;margin-top:12px}}#search{{flex:1;min-width:0;border:1px solid #d8e0ea;border-radius:8px;padding:9px 12px;font-size:14px;outline:none}}#search:focus{{border-color:#1c6b8c;box-shadow:0 0 0 3px #d9eef5}}
.nav-wrap{{max-width:1040px;margin:0 auto;padding:12px 20px 4px}}.nav-title{{font-size:12px;color:#718096;margin-bottom:7px}}#filters{{display:flex;gap:7px;overflow-x:auto;padding-bottom:8px;scrollbar-width:thin}}.chip{{flex:none;border:1px solid #cad6e2;background:#fff;color:#24445f;border-radius:18px;padding:6px 11px;font-size:12px;font-weight:650;cursor:pointer}}.chip:hover,.chip.active{{background:#12365a;color:#fff;border-color:#12365a}}
main{{max-width:1040px;margin:auto;padding:8px 20px 48px}}.result-line{{font-size:12px;color:#718096;margin:5px 0 14px}}.topic{{scroll-margin-top:155px;margin-bottom:24px}}.topic-head{{display:flex;align-items:baseline;gap:8px;margin:0 0 9px;border-bottom:1px solid #dce4ed;padding-bottom:7px}}.topic-head h2{{font-size:18px;color:#12365a;margin:0}}.topic-head span{{font-size:12px;color:#718096}}
.card{{background:#fff;border:1px solid #e3e9f0;border-radius:12px;padding:17px 18px;margin-bottom:11px;box-shadow:0 3px 12px rgba(28,48,74,.04)}}.card-top{{display:flex;justify-content:space-between;gap:10px}}.title{{font-size:16px;font-weight:750;color:#162b44;line-height:1.42}}.score{{flex:none;background:#fff3dc;color:#9a5b00;border-radius:5px;padding:3px 7px;font-size:11px;font-weight:700;height:max-content}}.score.high{{background:#e9f6f3;color:#176b5a}}.byline{{font-size:12px;color:#748296;margin-top:4px}}.tags{{margin-top:7px}}.tag{{display:inline-block;background:#edf3f7;color:#52677a;border-radius:10px;padding:2px 7px;font-size:10px;margin:0 4px 3px 0}}
.abstract{{font-size:12px;color:#46566b;background:#f8fafc;border-left:3px solid #8db4c7;padding:9px 11px;margin:11px 0;border-radius:0 6px 6px 0}}.abstract.zh{{background:#f5faf7;border-left-color:#58a581;margin-top:-6px}}.abstract b{{color:#12365a}}
.flow{{display:grid;grid-template-columns:1fr 18px 1fr 18px 1fr 18px 1fr;align-items:stretch;margin:11px 0}}.node{{background:#f2f6f9;border-radius:7px;padding:8px;font-size:11px;color:#40536a}}.node b{{display:block;color:#15506d;margin-bottom:3px}}.arrow{{display:flex;align-items:center;justify-content:center;color:#1c8090;font-weight:800}}
.actions{{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}}.btn{{display:inline-block;border-radius:7px;padding:7px 11px;font-size:11px;font-weight:700;text-decoration:none;border:0;cursor:pointer}}.btn.primary{{background:#12365a;color:#fff}}.btn.oa{{background:#e6f4ef;color:#176b4d}}.btn.more{{background:#eef2f6;color:#40536a}}.detail{{display:none;border-top:1px solid #e8edf2;margin-top:12px;padding-top:10px;font-size:12px;color:#526173}}.card.open .detail{{display:block}}.detail p{{margin:5px 0}}.empty{{background:#fff;border:1px dashed #bdc9d6;border-radius:10px;padding:35px;text-align:center;color:#718096}}
@media(max-width:700px){{.top-inner{{padding:12px 14px 10px}}.nav-wrap,main{{padding-left:14px;padding-right:14px}}h1{{font-size:18px}}.summary{{gap:10px}}.flow{{grid-template-columns:1fr}}.arrow{{transform:rotate(90deg);height:18px}}.card{{padding:14px}}.topic{{scroll-margin-top:145px}}}}
</style></head><body>
<div class="top"><div class="top-inner"><h1>电力系统文献雷达</h1><div class="meta">{generated_at} · {html_escape(title)}</div>
<div class="summary"><span><b>{len(items)}</b>篇文献</span><span><b>{high}</b>篇高相关</span><span><b>{oa_count}</b>篇开放获取</span><span>最高相关度 <b>{score_max}</b></span></div>
<div class="search-row"><input id="search" type="search" placeholder="搜索标题、作者、关键词、摘要或研究概括"></div></div></div>
<div class="nav-wrap"><div class="nav-title">语言分区</div><div id="langfilters">
<button class="chip active" type="button" data-lang="ALL">全部 {len(items)}</button>
<button class="chip" type="button" data-lang="en">🌐 英文 {en_count}</button>
<button class="chip" type="button" data-lang="zh">🇨🇳 中文 {zh_count}</button>
</div><div class="nav-title" style="margin-top:8px">按关键词查看（点击后，同关键词文献会集中显示）</div><div id="filters">
<button class="chip active" type="button" data-kw="ALL">全部 {len(items)}</button>
{"".join(f'<button class="chip" type="button" data-kw="{html_escape(kw)}">{html_escape(kw)} {len(pids)}</button>' for kw, pids in sorted_kws)}
</div></div><main id="papers"><div id="resultLine" class="result-line"></div><div id="main"></div></main>
<script>
const PAPERS={papers_json};let activeKw='ALL';let activeLang='ALL';
const attr=v=>String(v||'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll("'",'&#39;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function cardHTML(p){{
 const tags=p.kw_terms.slice(0,6).map(k=>`<span class="tag">${{attr(k)}}</span>`).join('');
 const source=p.url?`<a class="btn primary" href="${{attr(p.url)}}" target="_blank" rel="noopener">阅读原文 →</a>`:'';
 const oa=p.oa_url?`<a class="btn oa" href="${{attr(p.oa_url)}}" target="_blank" rel="noopener">OA 全文</a>`:'';
 const abstractZh=p.lang!=='zh'&&p.abstract_zh_short?`<div class="abstract zh"><b>中文翻译：</b>${{p.abstract_zh_short}}</div>`:(p.lang!=='zh'?'<div class="abstract zh"><b>中文翻译：</b>暂不可用</div>':'');
 const flow=p.has_analysis?`<div class="flow"><div class="node"><b>研究问题</b>${{p.problem||'待核实'}}</div><div class="arrow">→</div><div class="node"><b>方法路线</b>${{p.method||'待核实'}}</div><div class="arrow">→</div><div class="node"><b>创新/结果</b>${{p.innovation||'待核实'}}</div><div class="arrow">→</div><div class="node"><b>应用场景</b>${{p.application||'待核实'}}</div></div>`:'<div class="abstract">暂无自动概括，请结合原文阅读。</div>';
 return `<article class="card"><div class="card-top"><div class="title">${{p.title}}</div><span class="score ${{p.score>=7?'high':''}}">相关度 ${{p.score}}</span></div><div class="byline">${{p.authors||'作者未知'}} · ${{p.venue||'来源未知'}}${{p.year?' · '+p.year:''}}${{p.is_oa?' · OA':''}}</div><div class="tags">${{tags}}</div><div class="abstract"><b>${{p.lang==='zh'?'中文摘要：':'英文摘要：'}}</b>${{p.abstract_short||'暂无摘要，请打开原文核实。'}}</div>${{abstractZh}}${{flow}}<div class="actions">${{source}}${{oa}}<button class="btn more" type="button" data-action="toggle" aria-expanded="false">展开完整摘要与分析</button></div><div class="detail"><p><b>${{p.lang==='zh'?'完整中文摘要：':'完整英文摘要：'}}</b>${{p.abstract||'暂无摘要。'}}</p>${{p.lang!=='zh'?`<p><b>完整中文翻译：</b>${{p.abstract_zh||'暂不可用。'}}</p>`:''}}<p><b>借鉴价值：</b>${{p.value||'待核实。'}}</p><p><b>需要核实：</b>${{p.caveat||'请查阅原文。'}}</p></div></article>`;
}}
function render(){{
 const q=document.getElementById('search').value.trim().toLowerCase();
 const matches=PAPERS.filter(p=>(activeKw==='ALL'||p.kw_terms.includes(activeKw))&&(activeLang==='ALL'||p.lang===activeLang)&&(!q||p.search_blob.includes(q)));
 const groups=new Map();matches.forEach(p=>{{const key=activeKw==='ALL'?p.primary_keyword:activeKw;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);}});
 const langLabel=activeLang==='ALL'?'':(activeLang==='zh'?' · 中文文献':' · 英文文献');
 document.getElementById('resultLine').textContent=`当前显示 ${{matches.length}} 篇${{activeKw==='ALL'?'':' · 关键词：'+activeKw}}${{langLabel}}`;
 document.getElementById('main').innerHTML=matches.length?[...groups].map(([kw,rows])=>`<section class="topic"><div class="topic-head"><h2>${{attr(kw)}}</h2><span>${{rows.length}} 篇</span></div>${{rows.map(cardHTML).join('')}}</section>`).join(''):'<div class="empty">没有符合条件的文献</div>';
}}
function setKw(kw,updateHash=true,shouldScroll=updateHash){{activeKw=kw;document.querySelectorAll('.chip').forEach(b=>b.classList.toggle('active',b.dataset.kw===kw));render();if(updateHash)history.replaceState(null,'',kw==='ALL'?location.pathname+location.search:'#kw='+encodeURIComponent(kw));if(shouldScroll)requestAnimationFrame(()=>document.getElementById('papers').scrollIntoView({{behavior:'smooth'}}));}}
document.getElementById('filters').addEventListener('click',e=>{{const b=e.target.closest('[data-kw]');if(b)setKw(b.dataset.kw);}});
document.getElementById('langfilters').addEventListener('click',e=>{{const b=e.target.closest('[data-lang]');if(!b)return;activeLang=b.dataset.lang;document.querySelectorAll('#langfilters .chip').forEach(c=>c.classList.toggle('active',c.dataset.lang===activeLang));render();}});
document.getElementById('search').addEventListener('input',render);
document.getElementById('main').addEventListener('click',e=>{{if(e.target.dataset.action==='toggle'){{const card=e.target.closest('.card');card.classList.toggle('open');const expanded=card.classList.contains('open');e.target.setAttribute('aria-expanded',String(expanded));e.target.textContent=expanded?'收起详情':'展开完整摘要与分析';}}}});
const hashKw=location.hash.startsWith('#kw=')?decodeURIComponent(location.hash.slice(4)):'ALL';const validHash=PAPERS.some(p=>p.kw_terms.includes(hashKw));setKw(validHash?hashKw:'ALL',false,validHash);
</script></body></html>"""


def _papers_json(items: list[dict[str, Any]]) -> str:
    """Serialize paper items as compact JSON for the dashboard JS."""
    records = []
    for item in items:
        analysis = item.get("interpretation") or {}
        kw_terms = []
        for hit in (item.get("hits") or []):
            t = hit.split(":", 1)[-1].strip() if ":" in hit else hit.strip()
            if t and t not in kw_terms:
                kw_terms.append(t)
        title = normalize_space(item.get("title") or "(无标题)")
        abstract = normalize_space(item.get("abstract"))
        abstract_zh = normalize_space(item.get("abstract_zh"))
        url = safe_http_url(item.get("url"))
        searchable = " ".join([
            title,
            ", ".join(item.get("authors") or []),
            normalize_space(item.get("venue")),
            " ".join(kw_terms),
            abstract,
            abstract_zh,
            " ".join(normalize_space(analysis.get(key)) for key in ("problem", "method", "innovation", "application", "value")),
        ]).lower()
        records.append({
            "lang": "zh" if contains_cjk(title) else "en",
            "title": html_escape(title),
            "authors": html_escape(", ".join(item.get("authors") or [])[:150]),
            "venue": html_escape(item.get("venue") or ""),
            "year": html_escape(str(item.get("year") or "")),
            "score": int(item.get("score", 0)),
            "is_oa": item.get("is_oa") is True,
            "url": url,
            "oa_url": safe_http_url(item.get("oa_url")),
            "doi": item.get("doi") or "",
            "abstract": html_escape(abstract),
            "abstract_short": html_escape(textwrap.shorten(abstract, width=420, placeholder=" …")),
            "abstract_zh": html_escape(abstract_zh),
            "abstract_zh_short": html_escape(textwrap.shorten(abstract_zh, width=500, placeholder=" …")),
            "kw_terms": kw_terms,
            "primary_keyword": kw_terms[0] if kw_terms else "其他",
            "search_blob": searchable,
            "has_analysis": bool(analysis and analysis.get("problem")),
            "problem": html_escape(analysis.get("problem", "")),
            "method": html_escape(analysis.get("method", "")),
            "innovation": html_escape(analysis.get("innovation", "")),
            "application": html_escape(analysis.get("application", "")),
            "value": html_escape(analysis.get("value", "")),
            "value_line": html_escape(textwrap.shorten(analysis.get("value", ""), width=150, placeholder="…")),
            "caveat": html_escape(analysis.get("caveat", "")),
        })
    # JSON 内嵌在 <script> 中，主动转义结束标签和行分隔符，防止异常元数据
    # 提前截断脚本或造成页面解析失败。
    return (json.dumps(records, ensure_ascii=False)
            .replace("</", "<\\/")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def extract_keyword_terms(item: dict[str, Any]) -> list[str]:
    """按命中顺序提取去重关键词，供邮件分区与网页分组复用。"""
    terms: list[str] = []
    seen: set[str] = set()
    for hit in item.get("hits") or []:
        term = (hit.split(":", 1)[-1] if ":" in hit else hit).strip()
        key = term.lower()
        if term and len(term) >= 2 and key not in seen:
            terms.append(term)
            seen.add(key)
    return terms


def group_items_by_primary_keyword(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """每篇论文只进入一个主关键词区块，避免总览重复和页面失控。"""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        terms = extract_keyword_terms(item)
        primary = terms[0] if terms else "其他"
        groups.setdefault(primary, []).append(item)
    return sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0].lower()))


def _chip_colors() -> list[str]:
    return ["#0f3460", "#e94560", "#f0a500", "#2d5a87", "#16213e", "#7b8496",
            "#1e6091", "#d68910", "#c44569", "#0d3b66", "#1a936f", "#e07a5f",
            "#3d5a80", "#98c1d9", "#ee6c4d", "#293241", "#a44a3f", "#4a7c59",
            "#6c5b7b", "#355c7d", "#c06c84", "#f67280", "#6c5b7b", "#4ecdc4",
            "#45b7d1"][:25]


def build_keyword_paper_map(items: list[dict[str, Any]]) -> dict[str, list[int]]:
    kw_to_papers: dict[str, list[int]] = {}
    for idx, item in enumerate(items, 1):
        for term in extract_keyword_terms(item):
            if term not in kw_to_papers:
                kw_to_papers[term] = []
            if idx not in kw_to_papers[term]:
                kw_to_papers[term].append(idx)
    return kw_to_papers


def html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def safe_http_url(value: Any) -> str:
    """只允许可导航的 HTTP(S) 地址，阻断外部元数据注入脚本协议。"""
    url = normalize_space(value)
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    return url if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""


def interpret_item(item: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    text = searchable_text(item)
    focus = infer_focus_terms(item)
    methods = infer_method_terms(text)
    resources = infer_resource_terms(text)

    problem = infer_problem(text, focus)
    method = infer_method_sentence(methods, text)
    innovation = infer_innovation_sentence(text, methods, resources)
    application = infer_application_sentence(text, resources)
    value = infer_value_sentence(focus, methods, resources)
    caveat = infer_caveat_sentence(item, text)

    return {
        "problem": problem,
        "method": method,
        "innovation": innovation,
        "application": application,
        "value": value,
        "caveat": caveat,
    }


def searchable_text(item: dict[str, Any]) -> str:
    return " ".join(
        normalize_space(item.get(k))
        for k in ("title", "abstract", "venue")
    ).lower()


def infer_focus_terms(item: dict[str, Any]) -> list[str]:
    labels = []
    seen = set()
    for hit in item.get("hits") or []:
        if ":" not in hit:
            continue
        group, term = hit.split(":", 1)
        if group in {"core", "configuration", "dispatch", "resources", "methods", "llm_power", "chinese"}:
            label = term.strip()
            key = label.lower()
            if label and key not in seen:
                labels.append(label)
                seen.add(key)
    return labels


def infer_method_terms(text: str) -> list[str]:
    method_map = [
        ("mixed-integer linear programming", "混合整数线性规划（MILP）"),
        ("milp", "混合整数线性规划（MILP）"),
        ("robust optimization", "鲁棒优化"),
        ("distributionally robust", "分布鲁棒优化"),
        ("stochastic optimization", "随机优化"),
        ("chance-constrained", "机会约束优化"),
        ("model predictive control", "模型预测控制（MPC）"),
        ("reinforcement learning", "强化学习"),
        ("deep reinforcement learning", "深度强化学习"),
        ("graph neural", "图神经网络"),
        ("machine learning", "机器学习"),
        ("deep learning", "深度学习"),
        ("admm", "ADMM 分解协调"),
        ("benders", "Benders 分解"),
        ("decomposition", "分解协调算法"),
        ("optimal power flow", "最优潮流（OPF）"),
        ("unit commitment", "机组组合"),
        ("economic dispatch", "经济调度"),
    ]
    return unique_labels(label for needle, label in method_map if needle in text)


def infer_resource_terms(text: str) -> list[str]:
    resource_map = [
        ("energy storage", "储能"),
        ("battery", "电池储能"),
        ("demand response", "需求响应"),
        ("virtual power plant", "虚拟电厂"),
        ("distributed energy", "分布式能源"),
        ("renewable", "新能源"),
        ("photovoltaic", "光伏"),
        ("wind", "风电"),
        ("microgrid", "微电网"),
        ("integrated energy", "综合能源系统"),
        ("distribution network", "配电网"),
        ("active distribution", "主动配电网"),
    ]
    return unique_labels(label for needle, label in resource_map if needle in text)


def unique_labels(values: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        key = value.lower()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def infer_problem(text: str, focus: list[str]) -> str:
    if any(term in text for term in ["network reconfiguration", "topology optimization"]):
        return "围绕配电网/电网拓扑重构或网架优化，试图在运行安全、损耗、灵活性和新能源接入之间取得更优平衡。"
    if any(term in text for term in ["unit commitment", "economic dispatch", "optimal dispatch", "real-time dispatch"]):
        return "面向电力系统运行调度问题，核心是提升机组/资源组合、经济性、安全约束和实时响应能力。"
    if "optimal power flow" in text or "opf" in text:
        return "聚焦最优潮流问题，通常关心网络约束下的功率分配、成本、损耗、电压或安全边界。"
    if any(term in text for term in ["planning", "siting", "sizing", "capacity configuration"]):
        return "面向规划与容量配置问题，重点在资源选址定容、投资运行协同和长期不确定性处理。"
    if focus:
        return f"与 {', '.join(focus[:4])} 相关，可能关注电力系统配置、调度或灵活资源协同优化。"
    return "题名/摘要中没有足够细节，建议先阅读全文摘要以确认具体研究问题。"


def infer_method_sentence(methods: list[str], text: str) -> str:
    if methods:
        return f"从题名/摘要看，可能采用 {', '.join(methods[:5])} 等方法构建优化、预测或控制模型。"
    if any(term in text for term in ["framework", "model", "algorithm", "approach"]):
        return "摘要中出现模型/算法/框架类表述，但元数据不足以判断具体算法类型，需要阅读全文确认。"
    return "当前元数据未给出明确方法，建议重点查看论文的模型构建、约束条件和求解算法部分。"


def infer_innovation_sentence(text: str, methods: list[str], resources: list[str]) -> str:
    signals = []
    if len(resources) >= 2:
        signals.append(f"把 {', '.join(resources[:3])} 纳入同一优化框架，可能体现多资源协同建模。")
    if any(term in text for term in ["uncertain", "uncertainty", "stochastic", "robust", "chance-constrained"]):
        signals.append("显式处理新能源、负荷或市场不确定性，创新点可能在不确定性建模与保守性/经济性的权衡。")
    if any(term in text for term in ["real-time", "online", "dynamic"]):
        signals.append("强调实时/在线/动态决策，可能改进传统离线调度模型的时效性。")
    if any(term in text for term in ["learning", "neural", "reinforcement"]):
        signals.append("引入学习型方法，创新点可能在复杂状态空间下的策略近似、预测增强或快速决策。")
    if any(term in text for term in ["distributed", "decentralized", "multi-agent"]):
        signals.append("采用分布式或多主体思路，适合处理多区域、多微网或虚拟电厂协同调度。")
    if methods and not signals:
        signals.append(f"创新点可能体现在 {', '.join(methods[:3])} 与具体电力系统场景的结合、约束刻画或求解效率提升。")
    if not signals:
        signals.append("仅凭当前元数据难以判断实质创新，建议优先核查其相对已有 OPF/调度/配置模型的新约束、新场景或新算法。")
    return " ".join(signals[:3])


def infer_application_sentence(text: str, resources: list[str]) -> str:
    if resources:
        return f"可优先联想到 {', '.join(resources[:4])} 等场景下的规划、运行优化或调度策略评估。"
    if "market" in text:
        return "可能适用于电力市场出清、辅助服务、灵活性交易或市场约束下的调度决策。"
    if "resilience" in text or "restoration" in text:
        return "可能适用于韧性提升、故障恢复、灾后重构或配电网自愈控制。"
    return "应用场景需要结合全文算例判断，重点查看测试系统、数据来源和对比基线。"


def infer_value_sentence(focus: list[str], methods: list[str], resources: list[str]) -> str:
    parts = []
    if focus:
        parts.append(f"可用于补充 {', '.join(focus[:3])} 方向的近期文献线索")
    if methods:
        parts.append(f"可借鉴其 {', '.join(methods[:2])} 建模/求解思路")
    if resources:
        parts.append(f"可对比其对 {', '.join(resources[:2])} 的约束表达")
    if parts:
        return "；".join(parts) + "。"
    return "适合作为扩展阅读线索，重点判断其模型假设、算例系统和与你课题变量/约束的重合度。"


def infer_caveat_sentence(item: dict[str, Any], text: str) -> str:
    caveats = []
    if not normalize_space(item.get("abstract")):
        caveats.append("当前没有摘要，创新点判断主要来自题名和关键词，可靠性较低")
    if not normalize_doi(item.get("doi")):
        caveats.append("缺少 DOI，需核实出版状态和版本")
    if item.get("source") in {"arxiv", "semantic_scholar"}:
        caveats.append("若为预印本或开放元数据记录，需确认是否已有正式发表版本")
    if any(term in text for term in ["review", "survey"]):
        caveats.append("可能是综述类论文，更适合整理脉络而非直接作为方法创新参考")
    if not caveats:
        caveats.append("需要核实数学模型、约束集合、数据来源、算例系统和对比算法是否充分")
    return "；".join(caveats) + "。"


def source_fetch_limit(config: dict[str, Any], source: dict[str, Any], max_override: int | None) -> int:
    """返回单个来源的安全候选量，避免超过第三方 API 的单次请求上限。"""
    requested = int(max_override or source.get("max_results") or (config.get("profile") or {}).get("max_results_per_source", 25))
    caps = {
        "openalex": 200,
        "crossref": 1000,
        "semantic_scholar": 100,
        "ieee_xplore_api": 200,
        "elsevier_scopus_api": 25,
        "napstic_search": 200,
    }
    cap = caps.get(str(source.get("type")))
    return max(1, min(requested, cap)) if cap else max(1, requested)


def fetch_enabled_sources(config: dict[str, Any], since: dt.date, root: Path, max_override: int | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source in config.get("sources", []):
        if not source.get("enabled", False):
            continue
        limit = source_fetch_limit(config, source, max_override)
        source_type = source.get("type")
        try:
            if source_type == "openalex":
                items.extend(fetch_openalex(config, source, since, limit))
            elif source_type == "crossref":
                items.extend(fetch_crossref(config, source, since, limit))
            elif source_type == "arxiv":
                items.extend(fetch_arxiv(config, source, since, limit))
            elif source_type == "semantic_scholar":
                items.extend(fetch_semantic_scholar(config, source, since, limit))
            elif source_type == "ieee_xplore_api":
                items.extend(fetch_ieee(config, source, since, limit))
            elif source_type == "elsevier_scopus_api":
                items.extend(fetch_elsevier_scopus(config, source, since, limit))
            elif source_type == "rss":
                items.extend(fetch_rss(config, source, since, limit))
            elif source_type == "napstic_search":
                items.extend(fetch_napstic_search(config, source, since, limit))
            elif source_type == "napstic_journals":
                items.extend(fetch_napstic_journals(config, source, since, limit))
            else:
                print(f"[warn] unknown source type skipped: {source_type}", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ET.ParseError, http.client.IncompleteRead) as exc:
            print(f"[warn] source failed {source.get('name')}: {exc}", file=sys.stderr)
    items.extend(load_manual_exports(config, root))
    return items


def print_config_summary(config: dict[str, Any]) -> None:
    keywords = config.get("keywords") or {}
    enabled = [s.get("name") for s in config.get("sources", []) if s.get("enabled")]
    manual = config.get("manual_exports") or {}
    journal_filter = config.get("journal_filter") or {}
    print("Config OK")
    print(f"profile: {(config.get('profile') or {}).get('name')}")
    print(f"enabled sources: {', '.join(enabled) if enabled else '(none)'}")
    print(f"manual exports: {'on' if manual.get('enabled') else 'off'}")
    print(f"journal filter: {'on' if journal_filter.get('enabled') else 'off'} ({len(configured_journal_terms(config))} venues)")
    print(f"keyword groups: {', '.join(k for k in keywords.keys() if k != 'exclude')}")
    print(f"exclude terms: {len(keywords.get('exclude', []))}")


def print_dry_run(config: dict[str, Any]) -> None:
    print_config_summary(config)
    print("")
    print("Source queries:")
    for source in config.get("sources", []):
        status = "enabled" if source.get("enabled") else "disabled"
        print(f"- {source.get('name')} [{source.get('type')}, {status}]: {source_query(config, source)}")
    manual = config.get("manual_exports") or {}
    if manual.get("enabled"):
        print("- manual_exports:", ", ".join(manual.get("paths", [])))


def maybe_notify(config: dict[str, Any], md_path: Path, json_path: Path, html_path: Path, dash_path: Path, items: list[dict[str, Any]]) -> bool:
    notifications = config.get("notifications") or {}
    digest = md_path.read_text(encoding="utf-8")
    html_body = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
    count = len(items)
    enabled_count = 0
    delivered_count = 0

    email = notifications.get("email") or {}
    if email.get("enabled"):
        enabled_count += 1
        if send_email_digest(email, digest, md_path, json_path, html_body, dash_path, count):
            delivered_count += 1

    wechat = notifications.get("wechat") or {}
    if wechat.get("enabled"):
        enabled_count += 1
        if send_wechat_digest(wechat, items, md_path, count):
            delivered_count += 1

    webhook = notifications.get("webhook") or {}
    if webhook.get("enabled"):
        enabled_count += 1
        url = os.environ.get(webhook.get("url_env", "RADAR_WEBHOOK_URL"))
        if not url:
            print("[warn] webhook enabled but URL env is missing", file=sys.stderr)
        else:
            post_webhook(
                url,
                {
                    "text": f"Power-system radar found {count} records.",
                    "digest_path": str(md_path),
                    "json_path": str(json_path),
                },
            )
            delivered_count += 1

    return enabled_count > 0 and delivered_count == enabled_count


def send_email_digest(email_config: dict[str, Any], digest: str, md_path: Path, json_path: Path, html_body: str, dash_path: Path, count: int) -> bool:
    host = env_value(email_config.get("smtp_host_env", "RADAR_SMTP_HOST"))
    user = env_value(email_config.get("smtp_user_env", "RADAR_SMTP_USER"))
    password = env_value(email_config.get("smtp_password_env", "RADAR_SMTP_PASSWORD"))
    sender = env_value(email_config.get("from_env", "RADAR_EMAIL_FROM"), user)
    configured_recipients = email_config.get("recipients") or []
    if isinstance(configured_recipients, str):
        configured_recipients = configured_recipients.split(",")
    recipient_source = configured_recipients or env_value(
        email_config.get("to_env", "RADAR_EMAIL_TO")
    ).split(",")
    recipients = [address.strip() for address in recipient_source if address.strip()]
    missing = []
    if not host:
        missing.append(email_config.get("smtp_host_env", "RADAR_SMTP_HOST"))
    if not sender:
        missing.append(email_config.get("from_env", "RADAR_EMAIL_FROM"))
    if not recipients:
        missing.append(email_config.get("to_env", "RADAR_EMAIL_TO"))
    if missing:
        print(f"[warn] email enabled but env is missing: {', '.join(missing)}", file=sys.stderr)
        return False

    port = int(env_value(email_config.get("smtp_port_env", "RADAR_SMTP_PORT"), str(email_config.get("smtp_port", 465))))
    use_ssl = bool(email_config.get("use_ssl", port == 465))
    use_starttls = bool(email_config.get("use_starttls", port == 587))
    subject_prefix = email_config.get("subject_prefix", "[电力系统文献雷达]")

    message = EmailMessage()
    message["Subject"] = f"{subject_prefix} {count} 篇未推送精选文献"
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    # Multipart: HTML as primary body, plain-text fallback
    message.set_content(digest)
    if html_body.strip():
        message.add_alternative(html_body, subtype="html")

    message.add_attachment(
        md_path.read_bytes(),
        maintype="text",
        subtype="markdown",
        filename=md_path.name,
    )
    message.add_attachment(
        dash_path.read_bytes(),
        maintype="text",
        subtype="html",
        filename=dash_path.name,
    )
    message.add_attachment(
        json_path.read_bytes(),
        maintype="application",
        subtype="json",
        filename=json_path.name,
    )

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=30) as smtp:
        if use_starttls and not use_ssl:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)
    return True


def send_wechat_digest(wechat_config: dict[str, Any], items: list[dict[str, Any]], md_path: Path, count: int) -> bool:
    url = env_value(wechat_config.get("webhook_url_env", "RADAR_WECHAT_WEBHOOK_URL"))
    if not url:
        print("[warn] wechat enabled but webhook env is missing", file=sys.stderr)
        return False
    top_n = int(wechat_config.get("top_n", 8))
    content = render_wechat_markdown(items[:top_n], md_path, count)
    payload_type = wechat_config.get("type", "wecom_bot")
    if payload_type == "wecom_bot":
        payload = {"msgtype": "markdown", "markdown": {"content": content[:3900]}}
    else:
        payload = {"text": content[:3900], "digest_path": str(md_path), "count": count}
    post_webhook(url, payload)
    return True


def render_wechat_markdown(items: list[dict[str, Any]], md_path: Path, count: int) -> str:
    lines = [
        "### 电力系统文献雷达",
        f"> 本次筛出 {count} 条记录",
        "",
    ]
    if not items:
        lines.append("暂无符合阈值的新记录。")
    for i, item in enumerate(items, 1):
        title = item.get("title") or "(untitled)"
        score = item.get("score")
        venue = item.get("venue") or item.get("source") or ""
        url = item.get("url") or item.get("doi") or ""
        lines.append(f"{i}. **{title}**")
        lines.append(f"   分数: {score} | {venue}")
        if url:
            lines.append(f"   {url}")
    lines.extend(["", f"完整 digest: {md_path}"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Power-system academic radar")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "assets" / "power_system_radar_config.json"))
    parser.add_argument("--validate-config", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--since", help="ISO date override, for example 2026-07-01")
    parser.add_argument("--max-results", type=int, help="Override per-source max results")
    parser.add_argument(
        "--document-type", choices=("journal", "preprint", "conference"),
        help="Only deliver one document category for an independently scheduled run",
    )
    parser.add_argument("--no-state", action="store_true", help="Do not read or update seen-item state")
    args = parser.parse_args()

    root = Path.cwd()
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = root / config_path
    config = load_config(config_path)
    if args.document_type:
        # 每类计划必须只产生自己的邮件和附件；同时把输出分目录，避免同日运行相互覆盖。
        journal_filter = config.setdefault("journal_filter", {})
        journal_filter["enabled"] = True
        journal_filter["journal_articles_only"] = True
        journal_filter["allow_preprints"] = args.document_type == "preprint"
        journal_filter["allow_conference_papers"] = args.document_type == "conference"
        profile_config = config.setdefault("profile", {})
        base_output = str(profile_config.get("output_dir", "outputs/power-system-radar")).rstrip("/\\")
        profile_config["output_dir"] = f"{base_output}/{args.document_type}"
        type_labels = {"journal": "期刊论文", "preprint": "预印本", "conference": "会议论文"}
        email = (config.setdefault("notifications", {}).setdefault("email", {}))
        prefix = str(email.get("subject_prefix", "[电力系统文献雷达]")).strip()
        email["subject_prefix"] = f"{prefix} [{type_labels[args.document_type]}]"

    if args.validate_config:
        print_config_summary(config)
        return 0
    if args.dry_run:
        print_dry_run(config)
        return 0
    if not args.run:
        parser.error("Choose --validate-config, --dry-run, or --run")

    profile = config.get("profile") or {}
    lookback = int(profile.get("lookback_days", 14))
    since = dt.date.fromisoformat(args.since) if args.since else utc_today() - dt.timedelta(days=lookback)
    daily_target = max(0, int(profile.get("daily_target_items", 0)))
    # 中英分配额：宁缺毋滥。default 英文 10、中文 5；旧配置只有 daily_target_items 时全部按英文处理。
    target_en = max(0, int(profile.get("daily_target_en", daily_target or 10)))
    target_zh = max(0, int(profile.get("daily_target_zh", 0)))
    if not profile.get("daily_target_en") and not profile.get("daily_target_zh"):
        target_en, target_zh = daily_target, 0
    backfill_enabled = bool(profile.get("backfill_enabled", True))
    candidate_limit = args.max_results or int(profile.get("candidate_results_per_source", profile.get("max_results_per_source", 25)))
    backfill_days = max(lookback, int(profile.get("backfill_lookback_days", lookback)))
    state_path = None if args.no_state else resolve_path(profile.get("state_file"), root)
    with run_lock(state_path):
        seen = set() if args.no_state else read_state(state_path)
        fetched = fetch_enabled_sources(config, since, root, candidate_limit)
        items = dedupe_and_score(fetched, config, seen)
        if args.document_type:
            items = [item for item in items if document_type_category(item) == args.document_type]
        total_target = target_en + target_zh
        if total_target and len(items) < total_target and not args.since and backfill_enabled and backfill_days > lookback:
            backfill_since = utc_today() - dt.timedelta(days=backfill_days)
            print(
                f"[info] only {len(items)} unseen records in the {lookback}-day window; "
                f"backfilling never-sent records from {backfill_days} days"
            )
            fetched.extend(fetch_enabled_sources(config, backfill_since, root, candidate_limit))
            items = dedupe_and_score(fetched, config, seen)
            if args.document_type:
                items = [item for item in items if document_type_category(item) == args.document_type]
        if total_target:
            items = apply_language_caps(items, target_en, target_zh)
        md_path, json_path, html_path, dash_path = write_outputs(items, config, root)
        delivered = False
        if items:
            delivered = maybe_notify(config, md_path, json_path, html_path, dash_path, items)
        else:
            print("[info] no records passed quality/caps; skipping notification", file=sys.stderr)
        if not args.no_state:
            if delivered:
                state_keys = []
                for item in items:
                    state_keys.append(item["key"])
                    if item.get("dedupe_key"):
                        state_keys.append(item["dedupe_key"])
                write_state(state_path, state_keys)
            elif items:
                print("[warn] state not updated because no enabled notification completed successfully", file=sys.stderr)
    print(f"records: {len(items)}")
    print(f"digest: {md_path}")
    print(f"html:  {html_path}")
    print(f"dashboard: {dash_path}")
    print(f"json:  {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
