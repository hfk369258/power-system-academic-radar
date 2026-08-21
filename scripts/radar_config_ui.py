#!/usr/bin/env python3
"""Local visual configuration console for the academic radar.

The localhost-only UI manages up to twenty independent delivery profiles and
their local PowerShell credential files.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import radar_update


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSET_PATH = PLUGIN_ROOT / "assets" / "radar_control_panel.html"
PROFILE_FILES = {
    "basic": PLUGIN_ROOT / "assets" / "power_system_radar_config.json",
    "full": PLUGIN_ROOT / "assets" / "power_system_radar_config_full.json",
}
ENV_FILES = {
    "basic": Path(os.environ.get("RADAR_UI_BASIC_ENV", str(PLUGIN_ROOT / "radar.env.ps1"))),
    "full": Path(os.environ.get("RADAR_UI_FULL_ENV", str(PLUGIN_ROOT / "radar.env_full.ps1"))),
}
PROFILE_META = {
    "basic": {"task_name": "PowerSystemAcademicRadar_Basic", "default_time": "08:30"},
    "full": {"task_name": "PowerSystemAcademicRadar_Full", "default_time": "08:45"},
}
PROFILES_ROOT = PLUGIN_ROOT / "profiles"
PROFILES_REGISTRY = PROFILES_ROOT / "profiles.json"
MAX_PROFILES = 20

# 应用内一键升级（GitHub Releases）的后台任务管理器。
UPDATE_MANAGER = radar_update.UpdateManager()
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
CREDENTIAL_FIELDS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "SEMANTIC_SCHOLAR_API_KEY",
    "IEEE_XPLORE_API_KEY",
    "ELSEVIER_API_KEY",
    "RADAR_SMTP_HOST",
    "RADAR_SMTP_PORT",
    "RADAR_SMTP_USER",
    "RADAR_SMTP_PASSWORD",
    "RADAR_EMAIL_FROM",
    "RADAR_EMAIL_TO",
)
GROUP_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{1,40}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
ENV_LINE_RE = re.compile(
    r'^\s*\$env:([A-Za-z0-9_]+)\s*=\s*(?:"((?:`.|[^"])*)"|\'((?:\'\'|[^\'])*)\')\s*(?:#.*)?$'
)
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
DOCUMENT_TYPES = ("journal", "preprint", "conference")
DOCUMENT_TYPE_LABELS = {"journal": "期刊论文", "preprint": "预印本", "conference": "会议论文"}
LLM_TEST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def test_llm_connection(model: str, base_url: str, api_key: str) -> dict[str, Any]:
    """调用一次 OpenAI 兼容 chat/completions，验证端点、模型与密钥是否可用。

    只发一句极短的 ping（max_tokens=5），不保存任何状态；失败按常见原因
    翻译成中文提示，方便用户直接看懂是 Key 问题、路径问题还是网络问题。
    """
    api_key = str(api_key or "").strip()
    if not api_key:
        raise ConfigError("请先在“本机接口与账号”中填写 API Key")
    model = str(model or "").strip()
    if not model or len(model) > 120:
        raise ConfigError("模型名称不能为空")
    base_url = str(base_url or "").strip()
    if not re.match(r"^https?://", base_url, flags=re.I) or len(base_url) > 500:
        raise ConfigError("API 地址必须是有效的 http/https 地址")
    payload = {
        "model": model,
        "max_tokens": 64,
        "temperature": 0,
        "messages": [{"role": "user", "content": "ping"}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": LLM_TEST_UA,
    }
    request = urllib.request.Request(
        base_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        status = exc.code
        detail = ""
        try:
            server_message = (json.loads(exc.read(2048).decode("utf-8")) or {}).get("error", {}).get("message")
            if isinstance(server_message, str) and server_message:
                detail = f"（{server_message[:120]}）"
        except Exception:  # noqa: BLE001
            pass
        if status in (401, 403):
            raise ConfigError("API Key 无效或没有该模型的访问权限（HTTP 401/403）") from exc
        if status == 402:
            raise ConfigError(f"账户余额不足或欠费（HTTP 402）{detail}") from exc
        if status == 404:
            raise ConfigError("地址或模型不存在，请核对 API 地址末尾的 chat/completions 与模型名（404）") from exc
        if status == 429:
            raise ConfigError("请求被限流（429），请稍后再试或检查套餐额度") from exc
        raise ConfigError(f"服务端返回错误（HTTP {status}）{detail}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise ConfigError(f"无法连接到服务器：{reason}") from exc
    except TimeoutError as exc:
        raise ConfigError("连接超时，请检查网络或更换端点") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        data = json.loads(raw.decode("utf-8"))
        message = (data.get("choices") or [{}])[0].get("message") or {}
        # 推理类模型可能只返回 reasoning_content、content 置空，两者任一非空即视为成功
        reply = message.get("content") or message.get("reasoning_content") or ""
    except Exception:  # noqa: BLE001
        reply = ""
    if not reply:
        raise ConfigError("服务端响应格式异常，请确认这是 OpenAI 兼容的 chat/completions 地址")
    return {"model": model, "latency_ms": latency_ms, "reply": str(reply)[:40]}


def _no_window_flags() -> int:
    """Windows 下隐藏子进程终端窗口，避免控制台操作时弹出 PowerShell 窗口。"""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class ConfigError(ValueError):
    """User-correctable configuration error."""


def chinese_exception_message(exc: Exception) -> str:
    if isinstance(exc, ConfigError):
        message = str(exc).strip()
        if re.search(r"[\u4e00-\u9fff]", message):
            return message
        lowered = message.lower()
        if "access" in lowered and ("denied" in lowered or "permission" in lowered):
            return "权限不足，无法修改 Windows 计划任务；请以当前登录用户重新打开控制台"
        if "not found" in lowered or "cannot find" in lowered:
            return "找不到所需的本机文件或系统组件，请检查插件目录是否完整"
        return "本机操作失败，请检查控制台窗口中的运行状态后重试"
    if isinstance(exc, json.JSONDecodeError):
        return "配置文件格式错误，请恢复备份或检查 JSON 内容"
    if isinstance(exc, OSError):
        return "本机文件读写失败，请检查文件是否被占用以及当前账号权限"
    return "控制台发生未知错误，请重新启动后再试"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _stamp_to_iso(stamp: str) -> str:
    """把日报文件名里的 20260810_102258_277244 还原为 2026-08-10T10:22:58。"""
    digits = stamp.replace("_", "")
    if len(digits) < 14 or not digits[:14].isdigit():
        return ""
    return (
        f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        f"T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
    )


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """在同一目录内写临时文件后替换，避免进程中断留下半份配置。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _revision(config: dict[str, Any]) -> str:
    raw = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _text_revision(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _read_env_text(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig")


def parse_env_values(text: str) -> dict[str, str]:
    values = {name: "" for name in CREDENTIAL_FIELDS}
    for line in text.splitlines():
        match = ENV_LINE_RE.match(line)
        if not match or match.group(1) not in values:
            continue
        if match.group(2) is not None:
            value = re.sub(r"`(.)", r"\1", match.group(2))
        else:
            value = (match.group(3) or "").replace("''", "'")
        values[match.group(1)] = value
    return values


def validate_credentials(payload: dict[str, Any]) -> dict[str, str]:
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        raise ConfigError("凭据内容格式不正确")
    values: dict[str, str] = {}
    for name in CREDENTIAL_FIELDS:
        value = str(raw_values.get(name, ""))
        if "\n" in value or "\r" in value or "\x00" in value or len(value) > 4096:
            raise ConfigError(f"{name} 包含不允许的字符或内容过长")
        values[name] = value
    # _bounded_int 自身会抛出范围异常，原来的外层 `1 <= x <= 65535` 恒为 True，属死代码。
    if values["RADAR_SMTP_PORT"].strip():
        _bounded_int(values["RADAR_SMTP_PORT"].strip(), "SMTP 端口", 1, 65535)
    for name in ("RADAR_EMAIL_FROM",):
        if values[name].strip() and not EMAIL_RE.fullmatch(values[name].strip()):
            raise ConfigError(f"邮箱格式不正确：{values[name]}")
    recipients = [part.strip() for part in values["RADAR_EMAIL_TO"].split(",") if part.strip()]
    for address in recipients:
        if not EMAIL_RE.fullmatch(address):
            raise ConfigError(f"邮箱格式不正确：{address}")
    return values


def update_env_text(original: str, values: dict[str, str]) -> str:
    """Preserve comments/unknown variables while replacing the managed keys."""
    result: list[str] = []
    written: set[str] = set()
    for line in original.splitlines():
        match = ENV_LINE_RE.match(line)
        name = match.group(1) if match else ""
        if name in values:
            escaped = values[name].replace("'", "''")
            result.append(f"$env:{name} = '{escaped}'")
            written.add(name)
        else:
            result.append(line)
    if result and result[-1].strip():
        result.append("")
    for name in CREDENTIAL_FIELDS:
        if name not in written:
            escaped = values[name].replace("'", "''")
            result.append(f"$env:{name} = '{escaped}'")
    return "\n".join(result).rstrip() + "\n"


def _clean_terms(values: Any, label: str) -> list[str]:
    if not isinstance(values, list):
        raise ConfigError(f"{label}必须是列表")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value).strip()
        if not term:
            continue
        if len(term) > 160:
            raise ConfigError(f"{label}中的单项不能超过 160 个字符")
        key = term.casefold()
        if key not in seen:
            result.append(term)
            seen.add(key)
    return result


def _bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label}必须是整数") from exc
    if not minimum <= number <= maximum:
        raise ConfigError(f"{label}必须在 {minimum}–{maximum} 之间")
    return number


def _minutes_of_day(value: str) -> int:
    """把 HH:MM 转成当天分钟数，用于检查多任务触发时间是否过于集中。"""
    hours, minutes = value.split(":", 1)
    return int(hours) * 60 + int(minutes)


def editable_view(
    config: dict[str, Any],
    profile_key: str,
    profile_meta_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    profile = config.get("profile") or {}
    email = ((config.get("notifications") or {}).get("email") or {})
    llm = config.get("llm_interpretation") or {}
    schedule = config.get("schedule") or {}
    type_schedules = config.get("type_schedules") or {}
    profile_meta = profile_meta_override or PROFILE_META.get(profile_key, {})
    groups = [
        {"name": name, "terms": list(terms or [])}
        for name, terms in (config.get("keywords") or {}).items()
    ]
    sources = [
        {
            "name": source.get("name", source.get("type", "未命名数据源")),
            "type": source.get("type", ""),
            "enabled": bool(source.get("enabled")),
            "max_results": int(source.get("max_results", 25)),
        }
        for source in (config.get("sources") or [])
    ]
    return {
        "profile_key": profile_key,
        "revision": _revision(config),
        "profile": {
            "daily_target_items": int(profile.get("daily_target_items", 10)),
            "daily_target_en": int(profile.get("daily_target_en", 10)),
            "daily_target_zh": int(profile.get("daily_target_zh", 5)),
            "backfill_enabled": bool(profile.get("backfill_enabled", False)),
            "lookback_days": int(profile.get("lookback_days", 14)),
            "backfill_lookback_days": int(profile.get("backfill_lookback_days", 365)),
            "candidate_results_per_source": int(profile.get("candidate_results_per_source", 200)),
        },
        "keyword_groups": groups,
        "sources": sources,
        "journal_filter": {
            "enabled": bool((config.get("journal_filter") or {}).get("enabled", True)),
            "allow_preprints": bool((config.get("journal_filter") or {}).get("allow_preprints", False)),
            "allow_conference_papers": bool(
                (config.get("journal_filter") or {}).get("allow_conference_papers", False)
            ),
        },
        "schedule": {
            "frequency": str(schedule.get("frequency", "daily")),
            "time": str(schedule.get("time", profile_meta.get("default_time", "08:30"))),
            "day_of_week": str(schedule.get("day_of_week", "Monday")),
            "task_name": str(profile_meta.get("task_name", "")),
        },
        "type_schedules": {
            document_type: {
                "enabled": bool(
                    (type_schedules.get(document_type) or {}).get(
                        "enabled", document_type == "journal"
                    )
                ),
                "frequency": str(
                    (type_schedules.get(document_type) or {}).get(
                        "frequency", schedule.get("frequency", "daily")
                    )
                ),
                "time": str(
                    (type_schedules.get(document_type) or {}).get(
                        "time",
                        profile_meta.get("default_time", "08:30")
                        if document_type == "journal"
                        else ("08:40" if document_type == "preprint" else "08:50"),
                    )
                ),
                "day_of_week": str(
                    (type_schedules.get(document_type) or {}).get(
                        "day_of_week", schedule.get("day_of_week", "Monday")
                    )
                ),
                "task_name": f'{profile_meta.get("task_name", "")}_{document_type.title()}',
            }
            for document_type in DOCUMENT_TYPES
        },
                "llm": {
            "enabled": bool(llm.get("enabled", True)),
            "provider": str(llm.get("provider", "deepseek")),
            # 显示运行时实际生效值：进程环境变量优先（如 opencodezen 的 free 档网关）；方案凭据覆盖在 ConfigStore.get 中补齐
            "model": os.environ.get("DEEPSEEK_MODEL", str(llm.get("model", "deepseek-v4-flash"))),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", str(llm.get("base_url", "https://api.deepseek.com/chat/completions"))),
        },
        "email": {
            "enabled": bool(email.get("enabled", True)),
            "recipients": list(email.get("recipients") or []),
            "subject_prefix": str(email.get("subject_prefix", "[电力系统文献雷达]")),
            "uses_environment_fallback": not bool(email.get("recipients")),
            "security": "ssl" if email.get("use_ssl", True) else ("starttls" if email.get("use_starttls") else "none"),
        },
        "auto_query": bool((config.get("queries") or {}).get("auto_from_keywords", True)),
    }


def apply_editable_payload(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Validate UI data and merge only explicitly editable fields."""
    if not isinstance(payload, dict):
        raise ConfigError("请求内容必须是 JSON 对象")
    updated = deepcopy(config)
    profile_input = payload.get("profile") or {}
    profile = updated.setdefault("profile", {})
    legacy_sum = int(profile.get("daily_target_en", 10)) + int(profile.get("daily_target_zh", 5))
    profile["daily_target_items"] = _bounded_int(
        profile_input.get("daily_target_items", legacy_sum), "每日篇数", 0, 100
    )
    profile["daily_target_en"] = _bounded_int(
        profile_input.get("daily_target_en", 10), "英文每日篇数", 0, 100
    )
    profile["daily_target_zh"] = _bounded_int(
        profile_input.get("daily_target_zh", 5), "中文每日篇数", 0, 100
    )
    profile["backfill_enabled"] = bool(profile_input.get("backfill_enabled", False))
    profile["lookback_days"] = _bounded_int(profile_input.get("lookback_days"), "近期窗口", 1, 90)
    profile["backfill_lookback_days"] = _bounded_int(
        profile_input.get("backfill_lookback_days"), "回填窗口", profile["lookback_days"], 3650
    )
    profile["candidate_results_per_source"] = _bounded_int(
        profile_input.get("candidate_results_per_source"), "每源候选量", 10, 1000
    )

    group_rows = payload.get("keyword_groups")
    if not isinstance(group_rows, list) or not group_rows:
        raise ConfigError("至少保留一个关键词分组")
    keywords: dict[str, list[str]] = {}
    for row in group_rows:
        if not isinstance(row, dict):
            raise ConfigError("关键词分组格式不正确")
        name = str(row.get("name", "")).strip()
        if not GROUP_NAME_RE.fullmatch(name):
            raise ConfigError(f"分组名“{name}”只能包含中英文、数字、横线或下划线")
        if name in keywords:
            raise ConfigError(f"关键词分组“{name}”重复")
        keywords[name] = _clean_terms(row.get("terms", []), f"分组“{name}”")
    updated["keywords"] = keywords

    existing_sources = {str(item.get("name")): item for item in updated.get("sources") or []}
    source_rows = payload.get("sources") or []
    if not isinstance(source_rows, list):
        raise ConfigError("数据源格式不正确")
    for row in source_rows:
        source = existing_sources.get(str(row.get("name"))) if isinstance(row, dict) else None
        if source is None:
            raise ConfigError("不能通过控制台创建未知数据源")
        source["enabled"] = bool(row.get("enabled"))
        source["max_results"] = _bounded_int(row.get("max_results"), "数据源候选量", 1, 1000)

    journal_input = payload.get("journal_filter") or {}
    journal = updated.setdefault("journal_filter", {})
    journal["enabled"] = bool(journal_input.get("enabled", True))
    journal["journal_articles_only"] = True
    journal["allow_preprints"] = bool(journal_input.get("allow_preprints", False))
    journal["allow_conference_papers"] = bool(journal_input.get("allow_conference_papers", False))

    schedule_rows = payload.get("type_schedules") or {}
    if not isinstance(schedule_rows, dict):
        raise ConfigError("分类推送计划格式不正确")
    validated_schedules: dict[str, dict[str, Any]] = {}
    for document_type in DOCUMENT_TYPES:
        row = schedule_rows.get(document_type) or {}
        frequency = str(row.get("frequency", "daily")).lower()
        schedule_time = str(row.get("time", "")).strip()
        day_of_week = str(row.get("day_of_week", "Monday"))
        label = DOCUMENT_TYPE_LABELS[document_type]
        if frequency not in {"daily", "weekly"}:
            raise ConfigError(f"{label}推送频率仅支持每天或每周")
        if not TIME_RE.fullmatch(schedule_time):
            raise ConfigError(f"{label}推送时间必须使用 HH:mm 格式")
        if day_of_week not in WEEKDAYS:
            raise ConfigError(f"{label}每周推送日期无效")
        validated_schedules[document_type] = {
            "enabled": bool(row.get("enabled", document_type == "journal")),
            "frequency": frequency,
            "time": schedule_time,
            "day_of_week": day_of_week,
        }
    updated["type_schedules"] = validated_schedules
    # 保留旧字段供旧版启动器兼容，值始终跟随期刊论文计划。
    updated["schedule"] = {
        key: validated_schedules["journal"][key]
        for key in ("frequency", "time", "day_of_week")
    }
    journal["allow_preprints"] = validated_schedules["preprint"]["enabled"]
    journal["allow_conference_papers"] = validated_schedules["conference"]["enabled"]

    llm_input = payload.get("llm") or {}
    llm = updated.setdefault("llm_interpretation", {})
    llm["enabled"] = bool(llm_input.get("enabled", True))
    llm["provider"] = str(llm_input.get("provider", "deepseek")).strip() or "deepseek"
    llm["model"] = str(llm_input.get("model", "deepseek-v4-flash")).strip()
    base_url = str(llm_input.get("base_url", "")).strip()
    if not llm["model"] or len(llm["model"]) > 120:
        raise ConfigError("模型名称不能为空或超过 120 个字符")
    if not re.match(r"^https?://", base_url, flags=re.I) or len(base_url) > 500:
        raise ConfigError("API 地址必须是有效的 http/https 地址")
    llm["base_url"] = base_url

    email_input = payload.get("email") or {}
    recipients = _clean_terms(email_input.get("recipients", []), "推送邮箱")
    for address in recipients:
        if not EMAIL_RE.fullmatch(address):
            raise ConfigError(f"邮箱格式不正确：{address}")
    notifications = updated.setdefault("notifications", {})
    email = notifications.setdefault("email", {})
    email["enabled"] = bool(email_input.get("enabled", True))
    email["recipients"] = recipients
    prefix = str(email_input.get("subject_prefix", "")).strip()
    if not prefix or len(prefix) > 80:
        raise ConfigError("邮件主题前缀需为 1–80 个字符")
    email["subject_prefix"] = prefix
    security = str(email_input.get("security", "ssl"))
    if security not in {"ssl", "starttls", "none"}:
        raise ConfigError("SMTP 加密方式无效")
    email["use_ssl"] = security == "ssl"
    email["use_starttls"] = security == "starttls"

    queries = updated.setdefault("queries", {})
    queries["auto_from_keywords"] = bool(payload.get("auto_query", True))
    return updated


class ConfigStore:
    def __init__(
        self,
        profile_files: dict[str, Path] | None = None,
        env_files: dict[str, Path] | None = None,
        profile_meta: dict[str, dict[str, str]] | None = None,
        profiles_root: Path | None = None,
        registry_path: Path | None = None,
        env_template: Path | None = None,
    ):
        using_defaults = profile_files is None
        initial_files = PROFILE_FILES if using_defaults else (profile_files or {})
        initial_env_files = ENV_FILES if using_defaults and env_files is None else (env_files or {})
        initial_meta = PROFILE_META if profile_meta is None else profile_meta
        self.profile_files: dict[str, Path] = dict(initial_files)
        self.env_files: dict[str, Path] = dict(initial_env_files)
        self.profile_meta: dict[str, dict[str, str]] = deepcopy(initial_meta)
        self.profiles_root = Path(profiles_root or PROFILES_ROOT).resolve()
        self.registry_path = Path(registry_path or (self.profiles_root / "profiles.json")).resolve()
        self.env_template = Path(env_template or (PLUGIN_ROOT / "radar.env.example.ps1"))
        # 显式注入旧式映射时维持原单元测试/第三方调用兼容；生产模式和指定 profiles_root
        # 的调用启用动态注册表。
        self._managed = using_defaults or profiles_root is not None or registry_path is not None
        self._lock = threading.RLock()
        # 计划任务 subprocess 可能耗时数十秒：用独立锁串行化任务操作，
        # 但绝不在持有配置锁时执行，避免删除方案期间整个控制台卡死。
        self._task_lock = threading.Lock()
        self._initialized = not self._managed
        self._history_cache: tuple[tuple[Any, ...], float, list[dict[str, Any]]] | None = None
        self._initial_files = dict(initial_files)
        self._initial_env_files = dict(initial_env_files)
        self._initial_meta = deepcopy(initial_meta)

    def _ensure_initialized(self) -> None:
        """延迟迁移，保证模块导入本身不产生文件写入或权限副作用。"""
        if self._initialized:
            return
        with self._lock:
            if not self._initialized:
                self._initialize_registry(
                    self._initial_files, self._initial_env_files, self._initial_meta
                )
                self._initialized = True

    @staticmethod
    def _validate_profile_id(profile: str) -> str:
        value = str(profile).strip()
        if not PROFILE_ID_RE.fullmatch(value):
            raise ConfigError("方案编号无效，只能使用小写字母、数字、横线或下划线")
        return value

    def _profile_dir(self, profile: str) -> Path:
        """解析受控子目录并再次校验父目录，阻断路径穿越。"""
        profile = self._validate_profile_id(profile)
        path = (self.profiles_root / profile).resolve()
        if path.parent != self.profiles_root:
            raise ConfigError("方案路径无效")
        return path

    def _read_registry(self) -> dict[str, Any]:
        registry = _read_json(self.registry_path)
        rows = registry.get("profiles")
        if not isinstance(rows, list) or not rows:
            raise ConfigError("方案注册表损坏或没有可用方案")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ConfigError("方案注册表格式不正确")
            profile = self._validate_profile_id(str(row.get("id", "")))
            if profile in seen:
                raise ConfigError("方案注册表存在重复编号")
            seen.add(profile)
        return registry

    def _refresh_mappings(self, registry: dict[str, Any]) -> None:
        self.profile_files = {}
        self.env_files = {}
        self.profile_meta = {}
        for row in registry["profiles"]:
            profile = str(row["id"])
            directory = self._profile_dir(profile)
            self.profile_files[profile] = directory / "config.json"
            self.env_files[profile] = directory / "credentials.env.ps1"
            self.profile_meta[profile] = {
                "task_name": str(row.get("task_prefix") or f"PowerSystemAcademicRadar_{profile}"),
                "default_time": str(row.get("default_time") or "08:30"),
            }

    def _initial_env_text(self) -> str:
        # 新方案绝不继承被克隆方案的密钥；只复制公开示例的字段说明或生成空模板。
        if self.env_template.exists():
            return self.env_template.read_text(encoding="utf-8-sig")
        return update_env_text("# 本机私密配置，请勿提交到版本库。\n", {key: "" for key in CREDENTIAL_FIELDS})

    def _initialize_registry(
        self,
        initial_files: dict[str, Path],
        initial_env_files: dict[str, Path],
        initial_meta: dict[str, dict[str, str]],
    ) -> None:
        with self._lock:
            self.profiles_root.mkdir(parents=True, exist_ok=True)
            if self.registry_path.exists():
                self._refresh_mappings(self._read_registry())
                return
            rows: list[dict[str, str]] = []
            for profile, source_path in initial_files.items():
                profile = self._validate_profile_id(profile)
                directory = self._profile_dir(profile)
                directory.mkdir(parents=True, exist_ok=True)
                config_path = directory / "config.json"
                env_path = directory / "credentials.env.ps1"
                if not config_path.exists():
                    shutil.copy2(source_path, config_path)
                if not env_path.exists():
                    source_env = initial_env_files.get(profile)
                    env_text = _read_env_text(source_env) if source_env and source_env.exists() else self._initial_env_text()
                    _atomic_write_text(env_path, env_text, "utf-8-sig")
                meta = initial_meta.get(profile) or {}
                rows.append({
                    "id": profile,
                    "name": "基础版" if profile == "basic" else ("完整版" if profile == "full" else profile),
                    "task_prefix": str(meta.get("task_name") or f"PowerSystemAcademicRadar_{profile}"),
                    "default_time": str(meta.get("default_time") or "08:30"),
                })
            if not rows:
                raise ConfigError("无法初始化方案：没有基础配置")
            registry = {"version": 1, "profiles": rows}
            _atomic_write_json(self.registry_path, registry)
            self._refresh_mappings(registry)

    def list_profiles(self) -> list[dict[str, str]]:
        self._ensure_initialized()
        with self._lock:
            if not self._managed:
                return [
                    {"id": key, "name": key, "task_prefix": str((self.profile_meta.get(key) or {}).get("task_name", ""))}
                    for key in self.profile_files
                ]
            return deepcopy(self._read_registry()["profiles"])

    def history(
        self, profile: str | None = None, document_type: str | None = None, limit: int = 200, refresh: bool = False
    ) -> list[dict[str, Any]]:
        """读取各方案 running 的日报与投递记录（outputs/**/history.jsonl），
        并兼容升级前的日报文件（status=legacy）。按时间倒序。

        结果带 60 秒 TTL 缓存：发送记录面板频繁刷新时避免每次全量重扫 outputs；
        refresh=True 强制重扫。
        """
        self._ensure_initialized()
        try:
            safe_limit = max(0, min(int(limit), 500))
        except (TypeError, ValueError):
            safe_limit = 200
        cache_key = (profile, document_type, safe_limit)
        if (
            not refresh
            and self._history_cache is not None
            and self._history_cache[0] == cache_key
            and time.time() - self._history_cache[1] < 60
        ):
            return self._history_cache[2]
        ids = [self._validate_profile_id(profile)] if profile else list(self.profile_files)
        entries: list[dict[str, Any]] = []
        seen_digests: set[str] = set()
        for pid in ids:
            outputs = self._profile_dir(pid) / "outputs"
            if not outputs.is_dir():
                continue
            for jsonl in sorted(outputs.rglob("history.jsonl")):
                try:
                    lines_text = jsonl.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    # 雷达进程可能正在写入（半截文件）：跳过该文件不影响整体面板
                    continue
                for line in lines_text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    entry = {
                        "profile": pid,
                        "timestamp": str(row.get("timestamp") or ""),
                        "document_type": str(row.get("document_type") or jsonl.parent.name),
                        "records": int(row.get("records") or 0),
                        "status": str(row.get("status") or "failed"),
                        "digest": str(row.get("digest") or ""),
                        "dashboard": str(row.get("dashboard") or ""),
                        "records_file": str(row.get("records_file") or ""),
                        "html": str(row.get("html") or ""),
                    }
                    entries.append(entry)
                    if entry["digest"]:
                        seen_digests.add(entry["digest"])
            for md in sorted(outputs.rglob("digest_*.md")):
                if md.name in seen_digests:
                    continue
                stamp = md.stem.removeprefix("digest_")
                record_file = md.parent / f"records_{stamp}.json"
                records = 0
                if record_file.exists():
                    try:
                        data = json.loads(record_file.read_text(encoding="utf-8"))
                        records = len(data) if isinstance(data, list) else 0
                    except (json.JSONDecodeError, OSError):
                        records = 0
                entries.append(
                    {
                        "profile": pid,
                        "timestamp": _stamp_to_iso(stamp),
                        "document_type": md.parent.name,
                        "records": records,
                        "status": "legacy",
                        "digest": md.name,
                        "dashboard": f"dashboard_{stamp}.html",
                        "records_file": record_file.name,
                        "html": f"digest_{stamp}.html",
                    }
                )
        if document_type:
            entries = [entry for entry in entries if entry["document_type"] == document_type]
        entries.sort(key=lambda entry: entry["timestamp"], reverse=True)
        result = entries[:safe_limit]
        self._history_cache = (cache_key, time.time(), result)
        return result

    @staticmethod
    def _validate_profile_name(name: Any) -> str:
        value = str(name).strip()
        if not value or len(value) > 80 or any(char in value for char in "\r\n\x00"):
            raise ConfigError("方案名称需为 1–80 个有效字符")
        return value

    def create_profile(self, name: Any, clone_from: str) -> dict[str, str]:
        self._ensure_initialized()
        if not self._managed:
            raise ConfigError("当前存储模式不支持新增方案")
        name = self._validate_profile_name(name)
        clone_from = self._validate_profile_id(clone_from)
        with self._lock:
            registry = self._read_registry()
            rows = registry["profiles"]
            if len(rows) >= MAX_PROFILES:
                raise ConfigError(f"最多支持 {MAX_PROFILES} 个推送方案")
            if clone_from not in {str(row["id"]) for row in rows}:
                raise ConfigError("用于克隆的方案不存在")
            if any(str(row.get("name", "")).casefold() == name.casefold() for row in rows):
                raise ConfigError("方案名称已存在")
            # ID 与显示名称分离，中文名称也能获得安全且不可猜测冲突的目录名。
            profile = f"profile_{uuid.uuid4().hex[:10]}"
            while any(str(row["id"]) == profile for row in rows):
                profile = f"profile_{uuid.uuid4().hex[:10]}"
            directory = self._profile_dir(profile)
            directory.mkdir(parents=True, exist_ok=False)
            try:
                config = deepcopy(_read_json(self._path(clone_from)))
                profile_config = config.setdefault("profile", {})
                profile_config["state_file"] = f"profiles/{profile}/state.json"
                profile_config["output_dir"] = f"profiles/{profile}/outputs"
                _atomic_write_json(directory / "config.json", config)
                _atomic_write_text(directory / "credentials.env.ps1", self._initial_env_text(), "utf-8-sig")
                row = {
                    "id": profile,
                    "name": name,
                    "task_prefix": f"PowerSystemAcademicRadar_{profile}",
                    "default_time": str((self.profile_meta.get(clone_from) or {}).get("default_time", "08:30")),
                }
                rows.append(row)
                _atomic_write_json(self.registry_path, registry)
            except Exception:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            self._refresh_mappings(registry)
            return deepcopy(row)

    def rename_profile(self, profile: str, name: Any) -> dict[str, str]:
        self._ensure_initialized()
        if not self._managed:
            raise ConfigError("当前存储模式不支持修改方案名称")
        profile = self._validate_profile_id(profile)
        name = self._validate_profile_name(name)
        with self._lock:
            registry = self._read_registry()
            target = next((row for row in registry["profiles"] if row["id"] == profile), None)
            if target is None:
                raise ConfigError("方案不存在")
            if any(row["id"] != profile and str(row.get("name", "")).casefold() == name.casefold() for row in registry["profiles"]):
                raise ConfigError("方案名称已存在")
            target["name"] = name
            _atomic_write_json(self.registry_path, registry)
            self._refresh_mappings(registry)
            return deepcopy(target)

    def _run_task_ops(self, profile: str, switch: str) -> None:
        """按 switch(-Disable/-Remove) 对方案的旧版混合任务与三个分类任务执行同一操作。"""
        meta = self.profile_meta.get(profile) or {}
        task_prefix = str(meta.get("task_name", ""))
        if not task_prefix:
            raise ConfigError("该方案缺少计划任务前缀")
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        for task_name in [task_prefix, *(f"{task_prefix}_{kind.title()}" for kind in DOCUMENT_TYPES)]:
            command = [
                str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(PLUGIN_ROOT / "scripts" / "setup_windows_task.ps1"),
                "-TaskName", task_name, "-PluginRoot", str(PLUGIN_ROOT), switch,
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False, creationflags=_no_window_flags())
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "计划任务操作失败").strip()
                raise ConfigError(chinese_exception_message(ConfigError(detail[-1200:])))

    def _disable_tasks(self, profile: str) -> None:
        """停用（保留定义、禁用触发）方案的全部计划任务，与「停止任务」文案一致。"""
        self._run_task_ops(profile, "-Disable")

    def _remove_tasks(self, profile: str) -> None:
        """删除方案的全部计划任务（删除方案时使用）。"""
        self._run_task_ops(profile, "-Remove")

    def delete_profile(self, profile: str) -> dict[str, str]:
        self._ensure_initialized()
        if not self._managed:
            raise ConfigError("当前存储模式不支持删除方案")
        profile = self._validate_profile_id(profile)
        with self._lock:
            registry = self._read_registry()
            rows = registry["profiles"]
            if len(rows) <= 1:
                raise ConfigError("至少保留一个推送方案")
            target = next((row for row in rows if row["id"] == profile), None)
            if target is None:
                raise ConfigError("方案不存在")
            source = self._profile_dir(profile)

        # 计划任务删除可能耗时数十秒：在配置锁之外执行，避免阻塞其他请求。
        with self._task_lock:
            self._remove_tasks(profile)

        trash_root = (self.profiles_root / ".trash").resolve()
        trash_root.mkdir(parents=True, exist_ok=True)
        destination = trash_root / f"{profile}_{uuid.uuid4().hex[:10]}"
        shutil.move(str(source), str(destination))
        try:
            with self._lock:
                registry = self._read_registry()
                registry["profiles"] = [row for row in registry["profiles"] if row["id"] != profile]
                _atomic_write_json(self.registry_path, registry)
                self._refresh_mappings(registry)
        except Exception:
            shutil.move(str(destination), str(source))
            raise
        result = deepcopy(target)
        result["trash_path"] = str(destination)
        return result

    def _path(self, profile: str) -> Path:
        self._ensure_initialized()
        profile = self._validate_profile_id(profile)
        try:
            return self.profile_files[profile]
        except KeyError as exc:
            raise ConfigError("方案不存在") from exc

    def get(self, profile: str) -> dict[str, Any]:
        with self._lock:
            view = editable_view(_read_json(self._path(profile)), profile, self.profile_meta.get(profile))
            env_text = _read_env_text(self.env_files.get(profile))
            credentials = parse_env_values(env_text)
            # 运行时实际生效的 LLM 端点/模型来自方案凭据文件（如 opencodezen 网关），与界面显示保持一致
            if credentials.get("DEEPSEEK_BASE_URL"):
                view["llm"]["base_url"] = credentials["DEEPSEEK_BASE_URL"]
            if credentials.get("DEEPSEEK_MODEL"):
                view["llm"]["model"] = credentials["DEEPSEEK_MODEL"]
            view["credentials"] = credentials
            view["credentials_revision"] = _text_revision(env_text)
            return view

    def save(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._path(profile)
        with self._lock:
            current = _read_json(path)
            expected = str(payload.get("revision", ""))
            if expected and expected != _revision(current):
                raise ConfigError("配置已被其他进程修改，请重新读取后再保存")
            updated = apply_editable_payload(current, payload)
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(updated, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            view = editable_view(updated, profile, self.profile_meta.get(profile))
            env_text = _read_env_text(self.env_files.get(profile))
            view["credentials"] = parse_env_values(env_text)
            view["credentials_revision"] = _text_revision(env_text)
            return view

    def save_credentials(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._path(profile)
        env_path = self.env_files.get(profile)
        if env_path is None:
            raise ConfigError("该配置没有对应的本机凭据文件")
        values = validate_credentials(payload)
        with self._lock:
            original = _read_env_text(env_path)
            expected = str(payload.get("revision", ""))
            if expected and expected != _text_revision(original):
                raise ConfigError("凭据已被其他进程修改，请重新读取后再保存")
            updated = update_env_text(original, values)
            if env_path.exists():
                shutil.copy2(env_path, env_path.with_suffix(env_path.suffix + ".bak"))
            env_path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=env_path.name + ".", suffix=".tmp", dir=env_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\n") as handle:
                    handle.write(updated)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, env_path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return {"values": values, "revision": _text_revision(updated)}

    def test_llm(self, profile: str, payload: dict[str, Any]) -> dict[str, Any]:
        """用界面当前值（或已保存凭据）实调一次模型，验证 Key/端点/模型名。"""
        self._path(profile)
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:
            env_text = _read_env_text(self.env_files.get(profile))
            api_key = (parse_env_values(env_text) or {}).get("DEEPSEEK_API_KEY", "")
        return test_llm_connection(model, base_url, api_key)

    def apply_schedule(self, profile: str) -> dict[str, Any]:
        config_path = self._path(profile)
        env_path = self.env_files.get(profile)
        meta = self.profile_meta.get(profile) or {}
        if env_path is None or not env_path.exists() or not meta.get("task_name"):
            raise ConfigError("该配置缺少计划任务或凭据文件映射")
        config = _read_json(config_path)
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        email = ((config.get("notifications") or {}).get("email") or {})
        source_types = {str(source.get("type")) for source in config.get("sources") or [] if source.get("enabled")}
        schedules = config.get("type_schedules") or {}
        applied: dict[str, dict[str, Any]] = {}
        # 任务操作与删除/停止方案串行，且都在配置锁之外执行
        with self._task_lock:
            # 迁移到分类任务时先移除旧版“全部类型混合推送”任务，避免重复发送。
            legacy_command = [
                str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(PLUGIN_ROOT / "scripts" / "setup_windows_task.ps1"),
                "-TaskName", str(meta["task_name"]), "-PluginRoot", str(PLUGIN_ROOT), "-Remove",
            ]
            legacy_result = subprocess.run(legacy_command, capture_output=True, text=True, timeout=60, check=False, creationflags=_no_window_flags())
            if legacy_result.returncode != 0:
                detail = (legacy_result.stderr or legacy_result.stdout or "旧计划任务移除失败").strip()
                raise ConfigError(chinese_exception_message(ConfigError(detail[-1200:])))
            for document_type in DOCUMENT_TYPES:
                row = schedules.get(document_type) or {}
                frequency = str(row.get("frequency", "daily")).lower()
                schedule_time = str(row.get("time", ""))
                day = str(row.get("day_of_week", "Monday"))
                if frequency not in {"daily", "weekly"} or not TIME_RE.fullmatch(schedule_time) or day not in WEEKDAYS:
                    raise ConfigError(f"请先保存有效的{DOCUMENT_TYPE_LABELS[document_type]}推送计划")
                task_name = f'{meta["task_name"]}_{document_type.title()}'
                command = [
                    str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(PLUGIN_ROOT / "scripts" / "setup_windows_task.ps1"),
                    "-TaskName", task_name, "-DailyTime", schedule_time,
                    "-Frequency", frequency.title(), "-DayOfWeek", day,
                    "-DocumentType", document_type,
                    "-PluginRoot", str(PLUGIN_ROOT), "-ConfigPath", str(config_path),
                    "-PythonExe", os.environ.get("RADAR_PYTHON_EXE", sys.executable), "-EnvFile", str(env_path), "-Force",
                ]
                enabled = bool(row.get("enabled", document_type == "journal"))
                if not enabled:
                    command.append("-Disable")
                if email.get("enabled"):
                    command.append("-EnableEmail")
                if "ieee_xplore_api" in source_types:
                    command.append("-EnableIEEE")
                if "elsevier_scopus_api" in source_types:
                    command.append("-EnableElsevier")
                completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False, creationflags=_no_window_flags())
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "计划任务更新失败").strip()
                    raise ConfigError(chinese_exception_message(ConfigError(detail[-1200:])))
                applied[document_type] = {
                    "enabled": enabled, "task_name": task_name, "frequency": frequency,
                    "time": schedule_time, "day_of_week": day,
                }
        # 错峰提醒：同一天触发且间隔不足 10 分钟的任务会并发请求外部 API，
        # 容易触发限流（如 Semantic Scholar 429）。仅提示、不阻止应用。
        warnings = []
        active = [(doc, row) for doc, row in applied.items() if row.get("enabled")]
        for (doc1, row1), (doc2, row2) in itertools.combinations(active, 2):
            if row1["frequency"] == row2["frequency"] == "weekly" and row1["day_of_week"] != row2["day_of_week"]:
                continue
            minutes = abs(_minutes_of_day(row2["time"]) - _minutes_of_day(row1["time"]))
            if 0 < minutes < 10:
                warnings.append(
                    f"{DOCUMENT_TYPE_LABELS[doc1]}（{row1['time']}）与"
                    f"{DOCUMENT_TYPE_LABELS[doc2]}（{row2['time']}）触发间隔仅 {minutes} 分钟，"
                    "建议错开 10 分钟以上，避免外部接口并发限流"
                )
        result: dict[str, Any] = {"schedules": applied}
        if warnings:
            result["warnings"] = warnings
        return result

    def stop_schedule(self, profile: str) -> dict[str, str]:
        """停用该方案的全部 Windows 计划任务（旧版混合任务与分类任务）。"""
        self._ensure_initialized()
        profile = self._validate_profile_id(profile)
        with self._task_lock:
            self._disable_tasks(profile)
        return {"profile": profile}

    def restore(self, profile: str) -> dict[str, Any]:
        path = self._path(profile)
        backup = path.with_suffix(path.suffix + ".bak")
        with self._lock:
            if not backup.exists():
                raise ConfigError("还没有可恢复的备份")
            # 原子写回（与 save 一致），避免 copy2 覆盖中途崩溃损坏配置
            _atomic_write_text(path, backup.read_text(encoding="utf-8"))
            return self.get(profile)

    def read_output_file(self, profile: str, name: str) -> tuple[bytes, str]:
        """读取方案 outputs 目录下的单个产物文件（日报/dashboard/记录 JSON），供发送记录面板直接打开。

        name 可含子目录（如 journal/digest_xxx.md）。文件名白名单 + resolve 后父目录校验，
        杜绝路径穿越读取 outputs 之外的任意文件。
        """
        profile = self._validate_profile_id(profile)
        parts = name.replace("\\", "/").split("/")
        if (
            not name
            or len(name) > 240
            or any(part in {"", ".", ".."} for part in parts)
            or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", name)
        ):
            raise ConfigError("文件名无效")
        outputs_root = (self._profile_dir(profile) / "outputs").resolve()
        path = (outputs_root / name.replace("\\", "/")).resolve()
        if not str(path).startswith(str(outputs_root) + os.sep) or not path.is_file():
            raise ConfigError("文件不存在")
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".md": "text/plain; charset=utf-8",
        }.get(path.suffix.lower(), "application/octet-stream")
        return path.read_bytes(), content_type

    def maintenance_targets(self) -> list[dict[str, Any]]:
        """列出 profiles 根目录下的敏感残留（删除方案回收目录、凭据备份），供用户在控制台手动清理。

        只列不删；删除需用户在前端逐项勾选确认后调用 cleanup_maintenance。
        """
        self._ensure_initialized()
        root = self.profiles_root
        targets: list[dict[str, Any]] = []
        for pattern, label in (
            (".trash", "已删除方案的回收目录（含旧凭据副本）"),
            (".env-backup", "旧版根级凭据备份"),
            (".private-backup-*", "私有备份目录（可能含凭据副本）"),
        ):
            for path in sorted(root.glob(pattern)):
                try:
                    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                except OSError:
                    size = 0
                targets.append({"path": str(path), "label": label, "size": size})
        return targets

    def cleanup_maintenance(self, raw_targets: Any) -> dict[str, Any]:
        """删除用户在控制台明确勾选的维护目标。

        只允许删除 profiles 根目录下的 .trash / .env-backup / .private-backup-* 隐藏目录，
        任何其他路径一律拒绝，绝不越界删除。
        """
        self._ensure_initialized()
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ConfigError("请先勾选要清理的目录")
        root = self.profiles_root.resolve()
        allowed_names = (".trash", ".env-backup")
        removed: list[str] = []
        refused: list[str] = []
        for raw in raw_targets:
            try:
                path = Path(str(raw)).resolve()
            except OSError:
                refused.append(str(raw))
                continue
            if (
                not str(path).startswith(str(root) + os.sep)
                or path == root
                or not (path.name in allowed_names or path.name.startswith(".private-backup-"))
                or not path.is_dir()
            ):
                refused.append(str(raw))
                continue
            shutil.rmtree(path)
            removed.append(str(path))
        return {"removed": removed, "refused": refused}


class RadarUIHandler(BaseHTTPRequestHandler):
    store = ConfigStore()
    server_version = "RadarConfigUI/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[ui] {self.address_string()} - {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _profile(self) -> str:
        return parse_qs(urlparse(self.path).query).get("profile", ["basic"])[0]

    def _require_local_request(self) -> None:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("控制台仅允许通过本机地址访问")
        origin = self.headers.get("Origin", "")
        if origin and (urlparse(origin).hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ConfigError("拒绝来自其他网站的请求")

    def _require_same_site_write(self) -> None:
        """写操作额外校验：浏览器同源 fetch 一定携带 Origin 或 Sec-Fetch-Site，
        而跨站 <form> 提交两者都不带——必须拒绝，防止本机恶意网页静默调用
        删除方案/停止任务/回滚配置等破坏性接口（CSRF / DNS rebinding）。"""
        origin = self.headers.get("Origin", "")
        if origin:
            hostname = (urlparse(origin).hostname or "").lower()
            if hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ConfigError("拒绝来自其他网站的请求")
            return
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if fetch_site not in {"same-origin", "same-site"}:
            raise ConfigError("拒绝缺少来源标记的写请求")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            self._require_local_request()
            if parsed.path == "/":
                self._send(HTTPStatus.OK, ASSET_PATH.read_bytes(), "text/html; charset=utf-8")
            elif parsed.path == "/api/config":
                self._json(HTTPStatus.OK, {"ok": True, "data": self.store.get(self._profile())})
            elif parsed.path == "/api/profiles":
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "profiles": self.store.list_profiles(),
                    "max_profiles": MAX_PROFILES,
                })
            elif parsed.path == "/api/history":
                query = parse_qs(parsed.query)
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "history": self.store.history(
                        profile=query.get("profile", [None])[0],
                        document_type=query.get("type", [None])[0],
                        limit=int((query.get("limit") or ["200"])[0]),
                        refresh=query.get("refresh", ["0"])[0] not in {"0", "false", ""},
                    ),
                })
            elif parsed.path == "/api/outputs/file":
                query = parse_qs(parsed.query)
                data, content_type = self.store.read_output_file(
                    query.get("profile", ["basic"])[0], str(query.get("name", [""])[0])
                )
                self._send(HTTPStatus.OK, data, content_type)
            elif parsed.path == "/api/maintenance":
                self._json(HTTPStatus.OK, {"ok": True, "targets": self.store.maintenance_targets()})
            elif parsed.path == "/api/update/check":
                try:
                    data = radar_update.check_update()
                except radar_update.UpdateError as exc:
                    raise ConfigError(str(exc)) from exc
                self._json(HTTPStatus.OK, {"ok": True, "data": data})
            elif parsed.path == "/api/update/status":
                self._json(HTTPStatus.OK, {"ok": True, "data": {
                    "current_version": radar_update.APP_VERSION,
                    "frozen": bool(getattr(sys, "frozen", False)),
                    **UPDATE_MANAGER.status(),
                }})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "页面不存在"})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": chinese_exception_message(exc)})

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ConfigError("Content-Length 无效") from exc
        if length <= 0 or length > 1_000_000:
            raise ConfigError("请求内容为空或过大")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("JSON 内容无效") from exc
        if not isinstance(value, dict):
            raise ConfigError("请求内容必须是 JSON 对象")
        return value

    def do_PUT(self) -> None:  # noqa: N802
        try:
            self._require_local_request()
            self._require_same_site_write()
            api_path = urlparse(self.path).path
            if api_path == "/api/profiles":
                renamed = self.store.rename_profile(self._profile(), self._body().get("name"))
                self._json(HTTPStatus.OK, {"ok": True, "profile": renamed, "message": "方案名称已更新"})
                return
            if api_path == "/api/config":
                saved = self.store.save(self._profile(), self._body())
                self._json(HTTPStatus.OK, {"ok": True, "data": saved, "message": "配置已保存"})
                return
            if api_path == "/api/credentials":
                saved = self.store.save_credentials(self._profile(), self._body())
                self._json(HTTPStatus.OK, {"ok": True, "data": saved, "message": "本机凭据已保存"})
                return
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": chinese_exception_message(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_local_request()
            self._require_same_site_write()
            api_path = urlparse(self.path).path
            if api_path == "/api/profiles":
                body = self._body()
                created = self.store.create_profile(body.get("name"), str(body.get("clone_from", "basic")))
                self._json(HTTPStatus.CREATED, {"ok": True, "profile": created, "message": "推送方案已创建"})
                return
            if api_path == "/api/llm/test":
                try:
                    result = self.store.test_llm(self._profile(), self._body())
                except ConfigError as exc:
                    # 连接失败是预期业务结果而非请求格式错误，用 200 + ok:false 返回
                    self._json(HTTPStatus.OK, {"ok": False, "error": str(exc)})
                    return
                self._json(HTTPStatus.OK, {"ok": True, "data": result, "message": "模型连接测试成功"})
                return
            if api_path == "/api/restore":
                restored = self.store.restore(self._profile())
                self._json(HTTPStatus.OK, {"ok": True, "data": restored, "message": "已恢复上一次保存前的配置"})
                return
            if api_path == "/api/schedule/apply":
                applied = self.store.apply_schedule(self._profile())
                self._json(HTTPStatus.OK, {"ok": True, "data": applied, "message": "Windows 推送计划已更新"})
                return
            if api_path == "/api/schedule/stop":
                self.store.stop_schedule(self._profile())
                self._json(HTTPStatus.OK, {"ok": True, "message": "Windows 推送计划已停止"})
                return
            if api_path == "/api/update/apply":
                body = self._body()
                try:
                    started = UPDATE_MANAGER.start(tag=body.get("tag") or None)
                except radar_update.UpdateError as exc:
                    raise ConfigError(str(exc)) from exc
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    **started,
                    "message": "升级任务已启动，请保持窗口打开，控制台稍后将自动重启",
                })
                return
            if api_path == "/api/maintenance/cleanup":
                cleaned = self.store.cleanup_maintenance(self._body().get("targets"))
                message = f"已清理 {len(cleaned['removed'])} 个目录" + (
                    f"，拒绝 {len(cleaned['refused'])} 个无效路径" if cleaned["refused"] else ""
                )
                self._json(HTTPStatus.OK, {"ok": True, "data": cleaned, "message": message})
                return
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": chinese_exception_message(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._require_local_request()
            self._require_same_site_write()
            if urlparse(self.path).path != "/api/profiles":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "接口不存在"})
                return
            deleted = self.store.delete_profile(self._profile())
            self._json(HTTPStatus.OK, {"ok": True, "profile": deleted, "message": "方案已移入回收目录"})
        except Exception as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": chinese_exception_message(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="电力系统文献雷达可视化配置台")
    parser.add_argument("--host", default="127.0.0.1", help="默认仅本机访问")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--pid-file",
        default=str(Path(tempfile.gettempdir()) / "power-system-academic-radar-ui.pid"),
    )
    args = parser.parse_args()
    pid_file = Path(args.pid_file).expanduser()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    # 端口被占用时自动向后找可用端口（最多 20 个），双击启动不再因撞端口直接失败。
    server: ThreadingHTTPServer | None = None
    port = args.port
    for offset in range(20):
        try:
            server = ThreadingHTTPServer((args.host, port), RadarUIHandler)
            break
        except OSError:
            port = args.port + offset + 1
    if server is None:
        print(
            f"无法启动控制台：端口 {args.port}–{args.port + 19} 均被占用。\n"
            "可能已有旧的文献雷达控制台在运行，请关闭旧窗口后重试。",
            file=sys.stderr,
        )
        return 2
    if port != args.port:
        print(f"端口 {args.port} 已被占用，本次改用 {port}。")
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    url = f"http://{args.host}:{port}/"
    print(f"文献雷达控制台：{url}")
    print("按 Ctrl+C 停止。")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if pid_file.exists() and pid_file.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
