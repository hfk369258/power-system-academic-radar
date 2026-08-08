import datetime as dt
import importlib.util
import http.client
import json
import sys
import tempfile
import unittest
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

    def test_notification_result_requires_every_enabled_channel(self) -> None:
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

    def test_main_backfills_unseen_items_when_recent_window_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            state_path.write_text(json.dumps({"seen": ["doi:10.1/old"]}), encoding="utf-8")
            config = {
                "profile": {
                    "lookback_days": 14,
                    "backfill_lookback_days": 365,
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
        stale = dict(fresh, title_cn="旧论文", source_id="stale1", online_date="2026-05-01 10:00:00")
        config = {"keywords": {"chinese": ["构网型"]}, "queries": {"auto_from_keywords": True}}
        source = {"name": "中文检索(NAPSTIC)", "type": "napstic_search", "size": 20, "pages": 2, "delay_seconds": 0}

        with mock.patch.object(
            radar.cn_napstic, "search_literature", side_effect=[([fresh, stale], 88), ([], 0)]
        ) as call_mock:
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


if __name__ == "__main__":
    unittest.main()
