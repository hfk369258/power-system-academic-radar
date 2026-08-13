import datetime as dt
import importlib.util
import http.client
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("power_system_radar.py")
SPEC = importlib.util.spec_from_file_location("power_system_radar", MODULE_PATH)
assert SPEC and SPEC.loader
radar = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(radar)


class RadarStateTests(unittest.TestCase):
    def test_ui_managed_keywords_generate_provider_specific_query(self):
        config = {
            "keywords": {
                "core": ["power system", "smart grid"],
                "dispatch": ["optimal dispatch"],
                "exclude": ["semiconductor"],
            },
            "queries": {"auto_from_keywords": True, "english": "legacy"},
        }
        plain = radar.build_default_query(config, "openalex")
        scopus = radar.build_default_query(config, "elsevier_scopus_api")
        self.assertIn('("power system" OR "smart grid")', plain)
        self.assertIn('("optimal dispatch")', plain)
        self.assertNotIn("semiconductor", plain)
        self.assertEqual(scopus, f"TITLE-ABS-KEY({plain})")
        self.assertEqual(
            radar.source_query(config, {"type": "elsevier_scopus_api", "query_override": "legacy override"}),
            scopus,
        )

    def test_historical_items_are_removed_from_report(self) -> None:
        config = {"scoring": {"min_score": 0}, "journal_filter": {"enabled": False}}
        old = {"title": "Historical power system scheduling paper", "doi": "10.1/old"}
        new = {"title": "New power system scheduling paper", "doi": "10.1/new"}

        result = radar.dedupe_and_score([old, new], config, {"doi:10.1/old"})

        self.assertEqual([item["doi"] for item in result], ["10.1/new"])

    def test_journal_only_filter_rejects_conference_metadata(self) -> None:
        config = {
            "journal_filter": {
                "enabled": True,
                "journal_articles_only": True,
                "allow_missing_document_type": True,
                "allowed_venues": ["IEEE Transactions on Power Systems"],
            }
        }
        journal = {"venue": "IEEE Transactions on Power Systems", "publication_type": "JournalArticle"}
        conference = {"venue": "IEEE Transactions on Power Systems", "publication_type": "Conference Paper"}
        missing_type = {"venue": "IEEE Transactions on Power Systems"}

        self.assertTrue(radar.journal_filter_match(journal, config)[0])
        self.assertFalse(radar.journal_filter_match(conference, config)[0])
        self.assertTrue(radar.journal_filter_match(missing_type, config)[0])

    def test_preprint_and_conference_have_independent_switches(self) -> None:
        config = {
            "journal_filter": {
                "enabled": True,
                "journal_articles_only": True,
                "allow_preprints": True,
                "allow_conference_papers": False,
                "allow_missing_venue": False,
            }
        }
        preprint = {"publication_type": "preprint", "venue": "arXiv"}
        conference = {"publication_type": "Conference Paper", "venue": "IEEE PES General Meeting"}
        self.assertEqual(radar.document_type_category(preprint), "preprint")
        self.assertTrue(radar.journal_filter_match(preprint, config)[0])
        self.assertFalse(radar.journal_filter_match(conference, config)[0])
        config["journal_filter"]["allow_preprints"] = False
        config["journal_filter"]["allow_conference_papers"] = True
        self.assertFalse(radar.journal_filter_match(preprint, config)[0])
        self.assertTrue(radar.journal_filter_match(conference, config)[0])

    def test_document_type_categories_can_be_selected_independently(self) -> None:
        items = [
            {"publication_type": "JournalArticle", "title": "journal"},
            {"publication_type": "preprint", "title": "preprint"},
            {"publication_type": "Conference Paper", "title": "conference"},
        ]
        selected = {
            category: [item["title"] for item in items if radar.document_type_category(item) == category]
            for category in ("journal", "preprint", "conference")
        }
        self.assertEqual(selected, {"journal": ["journal"], "preprint": ["preprint"], "conference": ["conference"]})

    def test_deepseek_normalization_keeps_abstract_translation(self) -> None:
        fallback = {"problem": "fallback"}
        result = radar.normalize_interpretation(
            {"abstract_zh": "这是中文翻译。", "problem": "研究问题"},
            fallback,
        )

        self.assertEqual(result["abstract_zh"], "这是中文翻译。")
        self.assertEqual(result["problem"], "研究问题")

    def test_deepseek_retries_when_translation_is_missing(self) -> None:
        item = {"abstract": "English abstract."}
        complete = {"abstract_zh": "中文翻译。", "problem": "问题"}

        with mock.patch.object(
            radar,
            "interpret_item_with_deepseek",
            side_effect=[{"abstract_zh": "", "problem": "问题"}, complete],
        ) as call_mock, mock.patch.object(radar.time, "sleep"):
            result = radar.interpret_item_with_deepseek_retry(
                item,
                {"attempts": 2, "retry_delay_seconds": 0},
                {},
                "test-key",
            )

        self.assertEqual(call_mock.call_count, 2)
        self.assertEqual(result["abstract_zh"], "中文翻译。")

    def test_dashboard_places_chinese_translation_after_english_abstract(self) -> None:
        item = {
            "title": "Journal paper",
            "abstract": "English abstract text.",
            "abstract_zh": "中文摘要翻译。",
            "hits": ["core:power system"],
            "score": 5,
            "journal_filter_hits": ["IEEE Transactions on Power Systems"],
        }

        html = radar.render_dashboard_html([item], {"profile": {"name": "test"}})

        card_template = html[html.index("return `<article"):]
        self.assertLess(card_template.index("英文摘要："), card_template.index("${abstractZh}"))
        self.assertIn("中文摘要翻译。", html)

    def test_current_batch_duplicates_are_merged_by_normalized_title(self) -> None:
        config = {"scoring": {"min_score": 0}, "journal_filter": {"enabled": False}}
        first = {"title": "Optimal Dispatch: A Practical Method", "source": "openalex"}
        second = {"title": "Optimal dispatch — a practical method", "source": "crossref"}

        result = radar.dedupe_and_score([first, second], config, set())

        self.assertEqual(len(result), 1)

    def test_equal_score_prefers_newer_publication(self) -> None:
        config = {"scoring": {"min_score": 0}, "journal_filter": {"enabled": False}}
        older = {"title": "Older unique scheduling study", "published": "2025-01-01"}
        newer = {"title": "Newer unique scheduling study", "published": "2026-01-01"}

        result = radar.dedupe_and_score([older, newer], config, set())

        self.assertEqual(result[0]["title"], newer["title"])

    def test_source_candidate_limit_obeys_provider_cap(self) -> None:
        config = {"profile": {"max_results_per_source": 25}}

        self.assertEqual(radar.source_fetch_limit(config, {"type": "openalex"}, 500), 200)
        self.assertEqual(radar.source_fetch_limit(config, {"type": "semantic_scholar"}, 200), 100)
        self.assertEqual(radar.source_fetch_limit(config, {"type": "elsevier_scopus_api"}, 200), 25)

    def test_truncated_source_response_does_not_abort_other_processing(self) -> None:
        config = {"sources": [{"name": "openalex", "type": "openalex", "enabled": True}]}

        with mock.patch.object(radar, "fetch_openalex", side_effect=http.client.IncompleteRead(b"partial")), mock.patch.object(
            radar, "load_manual_exports", return_value=[]
        ):
            result = radar.fetch_enabled_sources(config, radar.utc_today(), Path.cwd(), 20)

        self.assertEqual(result, [])

    def test_state_update_is_additive_and_leaves_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"seen": ["doi:old"]}), encoding="utf-8")

            radar.write_state(path, ["doi:new"])

            self.assertEqual(radar.read_state(path), {"doi:old", "doi:new"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_run_lock_rejects_a_second_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            with radar.run_lock(state_path):
                with self.assertRaises(RuntimeError):
                    with radar.run_lock(state_path):
                        pass

    def test_empty_items_skip_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("digest.md", "records.json", "digest.html", "dashboard.html")]
            for path in files:
                path.write_text("test", encoding="utf-8")
            config = {"notifications": {"email": {"enabled": True}, "wechat": {"enabled": True}}}

            with mock.patch.object(radar, "send_email_digest", return_value=True), mock.patch.object(
                radar, "send_wechat_digest", return_value=False
            ):
                delivered = radar.maybe_notify(config, *files, [])

            self.assertFalse(delivered)

    def test_partial_channel_success_counts_as_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("digest.md", "records.json", "digest.html", "dashboard.html")]
            for path in files:
                path.write_text("test", encoding="utf-8")
            item = {"title": "A paper", "score": 5}
            config = {"notifications": {"email": {"enabled": True}, "wechat": {"enabled": True}}}

            with mock.patch.object(radar, "send_email_digest", return_value=True), mock.patch.object(
                radar, "send_wechat_digest", return_value=False
            ):
                delivered = radar.maybe_notify(config, *files, [item])

            # 至少一个渠道送达即视为已推送，避免次日对已送达渠道重复轰炸
            self.assertTrue(delivered)

    def test_webhook_exception_does_not_crash_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = [root / name for name in ("digest.md", "records.json", "digest.html", "dashboard.html")]
            for path in files:
                path.write_text("test", encoding="utf-8")
            item = {"title": "A paper", "score": 5}
            config = {
                "notifications": {
                    "email": {"enabled": False},
                    "webhook": {"enabled": True, "url_env": "RADAR_WEBHOOK_URL"},
                }
            }

            with mock.patch.object(radar, "post_webhook", side_effect=TimeoutError("boom")), mock.patch.dict(
                os.environ, {"RADAR_WEBHOOK_URL": "http://127.0.0.1:9/hook"}
            ):
                delivered = radar.maybe_notify(config, *files, [item])

            self.assertFalse(delivered)

    def test_state_corruption_is_backed_up_not_silently_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"seen": [broken json', encoding="utf-8")

            result = radar.read_state(path)

            self.assertEqual(result, set())
            backups = list(path.parent.glob("state.json.corrupt-*"))
            self.assertEqual(len(backups), 1)
            self.assertFalse(path.exists())

    def test_fetch_enabled_sources_isolates_unexpected_source_exception(self) -> None:
        config = {
            "sources": [
                {"name": "openalex", "type": "openalex", "enabled": True},
                {"name": "crossref", "type": "crossref", "enabled": True},
            ]
        }
        with mock.patch.object(radar, "fetch_openalex", side_effect=ValueError("bad config value")), mock.patch.object(
            radar, "fetch_crossref", return_value=[{"title": "healthy"}]
        ), mock.patch.object(radar, "load_manual_exports", return_value=[]):
            result = radar.fetch_enabled_sources(config, radar.utc_today(), Path.cwd(), 20)

        self.assertEqual([item["title"] for item in result], ["healthy"])

    def test_run_lock_takes_over_when_owner_pid_is_dead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            lock_path = state_path.with_name(state_path.name + ".lock")
            lock_path.write_text(json.dumps({"pid": 99999999, "token": "dead", "started": "x"}), encoding="utf-8")

            with radar.run_lock(state_path):
                self.assertTrue(lock_path.exists())
            self.assertFalse(lock_path.exists())

    def test_feed_url_blocked_for_private_and_metadata_addresses(self) -> None:
        self.assertTrue(radar.feed_url_blocked("http://169.254.169.254/latest/meta-data"))
        self.assertTrue(radar.feed_url_blocked("http://127.0.0.1:8000/feed"))
        self.assertTrue(radar.feed_url_blocked("http://192.168.1.10/rss"))
        self.assertTrue(radar.feed_url_blocked("file:///etc/passwd"))
        self.assertFalse(radar.feed_url_blocked("https://ieeexplore.ieee.org/rss/TOC/example.xml"))
        self.assertFalse(radar.feed_url_blocked("https://export.arxiv.org/rss/cs"))

    def test_same_doi_different_title_formatting_merges_into_one_record(self) -> None:
        config = {
            "scoring": {"min_score": 0},
            "journal_filter": {"enabled": False},
            "keywords": {
                "core": ["optimal power flow"],
                "weights_allowed": {},
            },
        }
        first = {
            "title": "A Deep Learning Approach for Optimal Power Flow Calculation",
            "doi": "10.1/same-paper",
        }
        second = {
            "title": "A Deep-Learning Approach for Optimal Power-Flow Calculation",
            "doi": "10.1/same-paper",
        }
        result = radar.dedupe_and_score([first, second], config, set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["doi"], "10.1/same-paper")

    def test_seen_normalized_title_key_blocks_doi_keyed_item(self) -> None:
        """跨源同一论文：A 源按标题键入状态，B 源带 DOI 也必须判为已推送。"""
        config = {"scoring": {"min_score": 0}, "journal_filter": {"enabled": False}}
        item = {"title": "Grid Forming Energy Storage Systems Control Review", "doi": "10.2/new-doi"}
        title_key = radar.normalized_title_key(item)
        self.assertTrue(title_key)
        result = radar.dedupe_and_score([item], config, {title_key})
        self.assertEqual(result, [])

    def test_prune_old_outputs_only_removes_expired_report_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            old_digest = output_dir / "digest_20200101_000000_000000.md"
            old_dash = output_dir / "dashboard_20200101_000000_000000.html"
            fresh = output_dir / "digest_20260101_000000_000000.md"
            history = output_dir / "history.jsonl"
            for path in (old_digest, old_dash, fresh, history):
                path.write_text("x", encoding="utf-8")
            old_time = time.time() - 100 * 86400
            fresh_time = time.time() - 2 * 86400
            os.utime(old_digest, (old_time, old_time))
            os.utime(old_dash, (old_time, old_time))
            os.utime(fresh, (fresh_time, fresh_time))
            os.utime(history, (old_time, old_time))

            radar._prune_old_outputs(output_dir, 30)

            self.assertFalse(old_digest.exists())
            self.assertFalse(old_dash.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(history.exists())  # history 永不清理

    def test_prune_old_outputs_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            old = output_dir / "digest_20200101_000000_000000.md"
            old.write_text("x", encoding="utf-8")
            old_time = time.time() - 100 * 86400
            os.utime(old, (old_time, old_time))

            radar._prune_old_outputs(output_dir, 0)

            self.assertTrue(old.exists())

    def test_main_backfills_unseen_items_when_recent_window_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({"seen": ["doi:10.1/old"]}), encoding="utf-8")
            config = {
                "profile": {
                    "lookback_days": 14,
                    "backfill_lookback_days": 365,
                    "backfill_enabled": True,
                    "candidate_results_per_source": 200,
                    "daily_target_items": 2,
                    "state_file": str(state_path),
                },
                "scoring": {"min_score": 0},
                "journal_filter": {"enabled": False},
            }
            recent = [{"title": "Already delivered paper", "doi": "10.1/old"}]
            backfill = [
                {"title": "Unseen backfill paper one", "doi": "10.1/new-1"},
                {"title": "Unseen backfill paper two", "doi": "10.1/new-2"},
            ]
            output_paths = tuple(root / name for name in ("digest.md", "records.json", "digest.html", "dashboard.html"))

            with mock.patch.object(radar, "load_config", return_value=config), mock.patch.object(
                radar, "fetch_enabled_sources", side_effect=[recent, backfill]
            ) as fetch_mock, mock.patch.object(radar, "write_outputs", return_value=output_paths) as write_mock, mock.patch.object(
                radar, "maybe_notify", return_value=True
            ), mock.patch.object(sys, "argv", ["radar", "--run", "--config", "ignored.json"]):
                result = radar.main()

            self.assertEqual(result, 0)
            self.assertEqual(fetch_mock.call_count, 2)
            delivered_items = write_mock.call_args.args[0]
            self.assertEqual({item["doi"] for item in delivered_items}, {"10.1/new-1", "10.1/new-2"})
            self.assertTrue({"doi:10.1/new-1", "doi:10.1/new-2"}.issubset(radar.read_state(state_path)))

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
        # 惯例：query_override 只在 auto_from_keywords 关闭时生效（与 openalex/scopus 同源行为）
        manual = {"auto_from_keywords": False, "chinese": "legacy"}
        override = radar.source_query(
            {"keywords": config["keywords"], "queries": manual},
            {"type": "napstic_search", "query_override": "构网型 AND 储能"},
        )
        self.assertEqual(override, "构网型 AND 储能")

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
        stale = dict(fresh, title_cn="旧论文", source_id="stale1", doi="10.13335/x.2", online_date="2026-05-01 10:00:00")
        config = {"keywords": {"chinese": ["构网型"]}, "queries": {"auto_from_keywords": True}}
        source = {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "size": 20, "delay_seconds": 0}

        with mock.patch.object(
            radar.cn_napstic, "search_literature", return_value=([fresh, stale], 580)
        ) as call_mock:
            items = radar.fetch_napstic_search(config, source, dt.date(2026, 7, 1), 40)

        self.assertEqual(call_mock.call_count, 1)
        self.assertEqual(call_mock.call_args.args[0], "构网型")
        # 接口只有相关度排序、无法按日期过滤：全部保留，客户端按 online_date 降序（无日期沉底）
        self.assertEqual([item["title"] for item in items], ["构网型变流器控制", "旧论文"])
        self.assertEqual(items[0]["source"], "中文检索(NAPSTIC)")

    def test_napstic_search_orders_by_online_date_desc_with_missing_last(self) -> None:
        old = {"source_id": "a1", "title_cn": "无日期旧文一", "journal_cn": "电网技术", "year": "2024"}
        mid = dict(old, source_id="a2", title_cn="2025年中刊文", online_date="2025-06-01 00:00:00")
        latest = dict(old, source_id="a3", title_cn="2026年首发文", online_date="2026-03-01 00:00:00")
        config = {"keywords": {"chinese": ["储能"]}, "queries": {"auto_from_keywords": True}}
        source = {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "size": 20, "delay_seconds": 0}

        with mock.patch.object(radar.cn_napstic, "search_literature", return_value=([old, latest, mid], 99)):
            items = radar.fetch_napstic_search(config, source, dt.date(2026, 6, 1), 40)

        self.assertEqual([item["title"] for item in items], ["2026年首发文", "2025年中刊文", "无日期旧文一"])

    def test_napstic_search_dedupes_records_across_terms(self) -> None:
        shared = {
            "source_id": "shared1",
            "title_cn": "储能容量配置",
            "abstract_cn": "摘要",
            "journal_cn": "电力自动化设备",
            "year": "2026",
            "online_date": "2026-08-01 10:00:00",
            "detail_url": "https://search.napstic.cn/literature/periodical/010x001",
        }
        config = {"keywords": {"chinese": ["构网型", "储能"]}, "queries": {"auto_from_keywords": True}}
        source = {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "size": 20, "delay_seconds": 0}

        with mock.patch.object(
            radar.cn_napstic, "search_literature", side_effect=[([shared], 100), ([shared], 200)]
        ) as call_mock:
            items = radar.fetch_napstic_search(config, source, dt.date(2026, 7, 1), 40)

        self.assertEqual(call_mock.call_count, 2)
        self.assertEqual([call_mock.call_args_list[i].args[0] for i in range(2)], ["构网型", "储能"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "储能容量配置")

    def test_napstic_query_terms_uses_override_when_auto_disabled(self) -> None:
        config = {"keywords": {"chinese": ["构网型"]}, "queries": {"auto_from_keywords": False}}
        terms = radar.napstic_query_terms(
            config, {"type": "napstic_search", "query_override": "构网型/储能"}
        )
        self.assertEqual(terms, ["构网型/储能"])

    def test_journal_filter_respects_bypass_flag(self) -> None:
        config = {
            "journal_filter": {
                "enabled": True,
                "allow_missing_venue": False,
                "allowed_venues": ["IEEE Transactions on Power Systems"],
            }
        }
        outside = {"venue": "分布式能源", "publication_type": "journal"}
        self.assertFalse(radar.journal_filter_match(outside, config)[0])
        bypassed = dict(outside, bypass_journal_whitelist=True)
        self.assertTrue(radar.journal_filter_match(bypassed, config)[0])

    def test_llm_endpoint_prefers_env_override(self) -> None:
        llm_config = {"base_url": "https://api.deepseek.com/chat/completions"}
        self.assertEqual(
            radar.llm_endpoint(llm_config), "https://api.deepseek.com/chat/completions"
        )
        with mock.patch.dict("os.environ", {"DEEPSEEK_BASE_URL": "https://opencode.ai/zen/go/v1/chat/completions"}):
            self.assertEqual(
                radar.llm_endpoint(llm_config), "https://opencode.ai/zen/go/v1/chat/completions"
            )

    def test_interpret_item_with_deepseek_uses_env_endpoint_and_browser_ua(self) -> None:
        llm_config = {"base_url": "https://api.deepseek.com/chat/completions", "timeout_seconds": 5}
        sent = {}

        def fake_post(url, payload, headers, timeout=60):
            sent["url"] = url
            sent["ua"] = headers.get("User-Agent", "")
            sent["model"] = payload.get("model")
            return {"choices": [{"message": {"content": '{"abstract_zh": "译文", "problem": "问题"}'}}]}

        with mock.patch.dict(
            "os.environ",
            {
                "DEEPSEEK_BASE_URL": "https://opencode.ai/zen/v1/chat/completions",
                "DEEPSEEK_MODEL": "deepseek-v4-flash-free",
            },
        ), mock.patch.object(radar, "http_post_json", side_effect=fake_post):
            radar.interpret_item_with_deepseek({"title": "t"}, llm_config, {}, "test-key")

        self.assertEqual(sent["url"], "https://opencode.ai/zen/v1/chat/completions")
        self.assertEqual(sent["model"], "deepseek-v4-flash-free")
        self.assertTrue(sent["ua"].startswith("Mozilla/5.0"))

    def test_language_caps_split_english_and_chinese(self) -> None:
        items = [
            {"title": f"English paper {i}", "score": 100 - i, "url": ""} for i in range(12)
        ] + [
            {"title": f"中文论文{i}", "score": 50 - i, "url": ""} for i in range(7)
        ]

        capped = radar.apply_language_caps(items, 10, 5)

        titles = [i["title"] for i in capped]
        en = [t for t in titles if t.startswith("English")]
        zh = [t for t in titles if t.startswith("中文")]
        self.assertEqual(len(en), 10)
        self.assertEqual(len(zh), 5)
        self.assertEqual(en[0], "English paper 0")  # 英文按分降序
        self.assertEqual(zh[0], "中文论文0")  # 中文按分降序
        self.assertEqual(capped[0]["title"], "English paper 0")  # 英文在前

    def test_language_caps_allow_fewer_when_short(self) -> None:
        items = [{"title": "中文少文", "score": 9, "url": ""}]
        capped = radar.apply_language_caps(items, 10, 5)
        self.assertEqual([i["title"] for i in capped], ["中文少文"])

    def test_digest_markdown_splits_sections_by_language(self) -> None:
        en_item = {
            "title": "Optimal dispatch review",
            "venue": "IEEE Transactions on Power Systems",
            "year": "2026",
            "authors": ["A"],
            "doi": "",
            "url": "",
            "oa_url": "",
            "journal_filter_hits": ["IEEE Transactions on Power Systems"],
            "hits": ["core:power system"],
            "score": 9,
            "publication_type": "journal",
            "abstract": "abs",
            "is_oa": False,
        }
        zh_item = dict(en_item, title="储能容量优化配置", title_en="", score=8)
        md = radar.render_digest_markdown([en_item, zh_item], {"profile": {"name": "t"}})

        self.assertIn("英文文献", md)
        self.assertIn("中文文献", md)
        # 中文文献显示“中文摘要”，不出现误导性的英文摘要/中文翻译
        self.assertIn("**中文摘要：**", md)
        self.assertIn("**英文摘要：**", md)

    def test_llm_skips_native_chinese_items(self) -> None:
        zh_item = {"title": "储能容量优化配置", "abstract": "中文摘要本身无需翻译。", "url": ""}
        en_item = {"title": "Optimal dispatch review", "abstract": "English abstract.", "url": ""}
        config = {
            "llm_interpretation": {"enabled": True, "max_items": 0, "attempts": 1, "retry_delay_seconds": 0},
            "output_policy": {"full_analysis_requires_oa": True},
        }
        called: list[dict] = []

        def fake_retry(item, llm_config, fallback, api_key):
            called.append(item["title"])
            return {"abstract_zh": "译文", "problem": "问题"}

        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}), mock.patch.object(
            radar, "interpret_item", return_value={"problem": "规则"}
        ), mock.patch.object(radar, "interpret_item_with_deepseek_retry", side_effect=fake_retry):
            radar.enrich_interpretations([zh_item, en_item], config)

        self.assertEqual(called, ["Optimal dispatch review"])  # 只解读英文
        self.assertEqual(zh_item["interpretation_mode"], "rule")
        self.assertEqual(zh_item["abstract_zh"], "中文摘要本身无需翻译。")
        self.assertEqual(en_item["interpretation_mode"], "deepseek")

    def test_napstic_search_degrades_when_module_missing(self) -> None:
        with mock.patch.object(radar, "cn_napstic", None):
            items = radar.fetch_napstic_search(
                {"keywords": {}}, {"name": "x", "type": "napstic_search"}, dt.date(2026, 7, 1), 40
            )
        self.assertEqual(items, [])

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


class NapsticHttpTests(unittest.TestCase):
    """cn_napstic.http_get 的重试与节流行为（不访问真实网络）。"""

    @classmethod
    def setUpClass(cls):
        import cn_napstic

        cls.cn = cn_napstic

    def test_permanent_404_is_not_retried(self) -> None:
        attempts = {"count": 0}

        def fake_open(req, timeout=None):
            attempts["count"] += 1
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

        with mock.patch.object(self.cn.urllib.request, "urlopen", fake_open), mock.patch.object(
            self.cn.time, "sleep"
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                self.cn.http_get("https://search.napstic.cn/x", retries=3, delay=0)

        self.assertEqual(attempts["count"], 1)

    def test_transient_failure_is_retried_with_backoff(self) -> None:
        attempts = {"count": 0}

        def fake_open(req, timeout=None):
            attempts["count"] += 1
            if attempts["count"] < 4:
                raise TimeoutError("slow server")
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", None, None)

        with mock.patch.object(self.cn.urllib.request, "urlopen", fake_open), mock.patch.object(
            self.cn.time, "sleep"
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP Error 500"):
                self.cn.http_get("https://search.napstic.cn/x", retries=3, delay=0)

        self.assertEqual(attempts["count"], 4)

    def test_article_id_extraction_does_not_require_010_prefix(self) -> None:
        html = (
            '<div class="article_item">'
            '<h4><a href="/periodical/zzz/article-123" title="一篇论文"></a></h4>'
            '<span class="highLight">张三</span>'
            '<span class="submissionPage">1-5</span>'
            '<span class="abstracLabel">摘要：</span><span class="ignore">内容</span>'
            "</div></div></div>"
        )
        articles = self.cn.parse_article_items(html)

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0]["source_id"], "article-123")


if __name__ == "__main__":
    unittest.main()
