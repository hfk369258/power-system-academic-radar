import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


UI_PATH = Path(__file__).with_name("radar_config_ui.py")
spec = importlib.util.spec_from_file_location("radar_config_ui", UI_PATH)
ui = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ui)


def sample_config() -> dict:
    return {
        "profile": {
            "daily_target_items": 10,
            "lookback_days": 14,
            "backfill_lookback_days": 365,
            "candidate_results_per_source": 200,
            "state_file": "state.json",
        },
        "keywords": {"core": ["power system"], "dispatch": ["optimal dispatch"], "exclude": []},
        "queries": {"english": "legacy query"},
        "sources": [{"name": "openalex", "type": "openalex", "enabled": True, "max_results": 50}],
        "journal_filter": {"enabled": True, "journal_articles_only": True, "allowed_venues": ["Journal A"]},
        "notifications": {
            "email": {
                "enabled": True,
                "smtp_password_env": "SECRET_ENV",
                "to_env": "RADAR_EMAIL_TO",
                "subject_prefix": "[雷达]",
            }
        },
    }


class RadarConfigUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "basic.json"
        self.env_path = Path(self.temp.name) / "basic.env.ps1"
        self.path.write_text(json.dumps(sample_config(), ensure_ascii=False), encoding="utf-8")
        self.env_path.write_text("# keep me\n$env:DEEPSEEK_API_KEY = 'old-key'\n$env:UNMANAGED = 'stay'\n", encoding="utf-8-sig")
        self.store = ui.ConfigStore(
            {"basic": self.path},
            {"basic": self.env_path},
            {"basic": {"task_name": "TestRadar", "default_time": "08:30"}},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_editable_view_returns_local_credentials_for_maintenance(self):
        view = self.store.get("basic")
        self.assertEqual(view["credentials"]["DEEPSEEK_API_KEY"], "old-key")
        self.assertTrue(view["credentials_revision"])

    def test_keyword_and_recipient_crud_round_trip_preserves_uneditable_fields(self):
        payload = self.store.get("basic")
        payload["keyword_groups"].append({"name": "llm_power", "terms": ["LLM", "RAG", "LLM"]})
        payload["keyword_groups"][0]["terms"] = ["smart grid"]
        payload["email"]["recipients"] = ["tong78@example.com", "team@example.org"]
        payload["auto_query"] = True
        saved = self.store.save("basic", payload)

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["keywords"]["core"], ["smart grid"])
        self.assertEqual(raw["keywords"]["llm_power"], ["LLM", "RAG"])
        self.assertEqual(raw["notifications"]["email"]["recipients"], ["tong78@example.com", "team@example.org"])
        self.assertTrue(raw["queries"]["auto_from_keywords"])
        self.assertEqual(raw["profile"]["state_file"], "state.json")
        self.assertEqual(raw["journal_filter"]["allowed_venues"], ["Journal A"])
        self.assertEqual(saved["profile_key"], "basic")

    def test_invalid_email_is_rejected_without_changing_file(self):
        original = self.path.read_text(encoding="utf-8")
        payload = self.store.get("basic")
        payload["email"]["recipients"] = ["not-an-email"]
        with self.assertRaisesRegex(ui.ConfigError, "邮箱格式"):
            self.store.save("basic", payload)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_stale_revision_is_rejected(self):
        payload = self.store.get("basic")
        raw = sample_config()
        raw["profile"]["daily_target_items"] = 20
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ui.ConfigError, "其他进程"):
            self.store.save("basic", payload)

    def test_restore_returns_previous_saved_version(self):
        first = self.store.get("basic")
        first["profile"]["daily_target_items"] = 12
        saved = self.store.save("basic", first)
        self.assertEqual(saved["profile"]["daily_target_items"], 12)
        second = self.store.get("basic")
        second["profile"]["daily_target_items"] = 15
        self.store.save("basic", second)
        restored = self.store.restore("basic")
        self.assertEqual(restored["profile"]["daily_target_items"], 12)

    def test_llm_display_reflects_credential_overrides(self):
        view = self.store.get("basic")
        self.assertNotEqual(view["llm"]["base_url"], "https://opencode.ai/zen/v1/chat/completions")
        values = view["credentials"]
        values["DEEPSEEK_BASE_URL"] = "https://opencode.ai/zen/v1/chat/completions"
        values["DEEPSEEK_MODEL"] = "deepseek-v4-flash-free"
        self.store.save_credentials(
            "basic", {"revision": view["credentials_revision"], "values": values}
        )
        refreshed = self.store.get("basic")
        self.assertEqual(refreshed["llm"]["base_url"], "https://opencode.ai/zen/v1/chat/completions")
        self.assertEqual(refreshed["llm"]["model"], "deepseek-v4-flash-free")

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ui.ConfigError, "方案编号无效"):
            self.store.get("../../secret")

    def test_language_quotas_and_backfill_switch_are_saved(self):
        payload = self.store.get("basic")
        payload["profile"]["daily_target_en"] = 12
        payload["profile"]["daily_target_zh"] = 4
        payload["profile"]["backfill_enabled"] = True
        saved = self.store.save("basic", payload)
        self.assertEqual(saved["profile"]["daily_target_en"], 12)
        self.assertEqual(saved["profile"]["daily_target_zh"], 4)
        self.assertTrue(saved["profile"]["backfill_enabled"])
        viewed = self.store.get("basic")
        self.assertEqual(viewed["profile"]["daily_target_en"], 12)
        self.assertEqual(viewed["profile"]["daily_target_zh"], 4)
        self.assertTrue(viewed["profile"]["backfill_enabled"])

    def test_legacy_payload_still_keeps_daily_target_items(self):
        payload = self.store.get("basic")
        payload["profile"]["daily_target_items"] = 7
        saved = self.store.save("basic", payload)
        self.assertEqual(saved["profile"]["daily_target_items"], 7)

    def test_stop_schedule_disables_all_tasks(self):
        with mock.patch.object(ui.ConfigStore, "_disable_tasks", return_value=None) as disable_mock:
            result = self.store.stop_schedule("basic")
        self.assertEqual(result, {"profile": "basic"})
        disable_mock.assert_called_once_with("basic")

    def test_credentials_round_trip_preserves_unmanaged_lines_and_creates_backup(self):
        view = self.store.get("basic")
        values = view["credentials"]
        values["DEEPSEEK_API_KEY"] = "new'key"
        values["RADAR_SMTP_PORT"] = "465"
        values["RADAR_SMTP_USER"] = "sender@example.com"
        result = self.store.save_credentials(
            "basic", {"revision": view["credentials_revision"], "values": values}
        )
        text = self.env_path.read_text(encoding="utf-8-sig")
        self.assertIn("$env:DEEPSEEK_API_KEY = 'new''key'", text)
        self.assertIn("$env:UNMANAGED = 'stay'", text)
        self.assertEqual(result["values"]["RADAR_SMTP_PORT"], "465")
        self.assertTrue(self.env_path.with_suffix(".ps1.bak").exists())

    def test_invalid_credential_email_is_rejected(self):
        view = self.store.get("basic")
        view["credentials"]["RADAR_EMAIL_TO"] = "broken-address"
        with self.assertRaisesRegex(ui.ConfigError, "邮箱格式"):
            self.store.save_credentials(
                "basic", {"revision": view["credentials_revision"], "values": view["credentials"]}
            )

    def test_schedule_settings_round_trip_and_apply_command(self):
        payload = self.store.get("basic")
        payload["type_schedules"]["journal"] = {
            "enabled": True, "frequency": "weekly", "time": "09:20", "day_of_week": "Friday"
        }
        payload["type_schedules"]["preprint"] = {
            "enabled": True, "frequency": "daily", "time": "09:30", "day_of_week": "Monday"
        }
        saved = self.store.save("basic", payload)
        self.assertEqual(saved["type_schedules"]["journal"]["frequency"], "weekly")
        self.assertTrue(saved["type_schedules"]["preprint"]["enabled"])
        with mock.patch.object(ui.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
            applied = self.store.apply_schedule("basic")
        self.assertEqual(run.call_count, 4)
        self.assertIn("-Disable", run.call_args_list[0].args[0])
        command = run.call_args_list[1].args[0]
        self.assertIn("-Frequency", command)
        self.assertIn("Weekly", command)
        self.assertIn("Friday", command)
        self.assertIn("-DocumentType", command)
        self.assertEqual(applied["schedules"]["journal"]["time"], "09:20")
        disabled_command = run.call_args_list[3].args[0]
        self.assertIn("-Disable", disabled_command)

    def test_invalid_schedule_is_rejected(self):
        payload = self.store.get("basic")
        payload["type_schedules"]["conference"]["time"] = "25:99"
        with self.assertRaisesRegex(ui.ConfigError, "HH:mm"):
            self.store.save("basic", payload)

    def test_system_errors_are_translated_to_chinese(self):
        message = ui.chinese_exception_message(ui.ConfigError("Access is denied"))
        self.assertIn("权限不足", message)
        file_message = ui.chinese_exception_message(OSError("file busy"))
        self.assertIn("文件读写失败", file_message)

    def test_schedule_command_failure_does_not_expose_english_error(self):
        payload = self.store.get("basic")
        self.store.save("basic", payload)
        with mock.patch.object(ui.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="Access is denied")
            with self.assertRaisesRegex(ui.ConfigError, "权限不足"):
                self.store.apply_schedule("basic")


class DynamicProfileStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.basic = self.root / "legacy-basic.json"
        self.full = self.root / "legacy-full.json"
        self.basic_env = self.root / "legacy-basic.env.ps1"
        self.full_env = self.root / "legacy-full.env.ps1"
        self.template = self.root / "radar.env.example.ps1"
        self.basic.write_text(json.dumps(sample_config(), ensure_ascii=False), encoding="utf-8")
        full_config = sample_config()
        full_config["profile"]["daily_target_items"] = 20
        self.full.write_text(json.dumps(full_config, ensure_ascii=False), encoding="utf-8")
        self.basic_env.write_text("$env:DEEPSEEK_API_KEY = 'top-secret'\n", encoding="utf-8-sig")
        self.full_env.write_text("$env:DEEPSEEK_API_KEY = 'full-secret'\n", encoding="utf-8-sig")
        self.template.write_text("# 公开模板\n$env:DEEPSEEK_API_KEY = ''\n", encoding="utf-8-sig")
        self.profiles_root = self.root / "profiles"
        self.store = ui.ConfigStore(
            {"basic": self.basic, "full": self.full},
            {"basic": self.basic_env, "full": self.full_env},
            {
                "basic": {"task_name": "Radar_Basic", "default_time": "08:30"},
                "full": {"task_name": "Radar_Full", "default_time": "08:45"},
            },
            profiles_root=self.profiles_root,
            env_template=self.template,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_initial_registry_keeps_basic_and_full(self):
        rows = self.store.list_profiles()
        self.assertEqual([row["id"] for row in rows], ["basic", "full"])
        self.assertTrue((self.profiles_root / "profiles.json").exists())
        self.assertEqual(self.store.get("full")["profile"]["daily_target_items"], 20)

    def test_create_clones_config_without_credentials_and_uses_independent_paths(self):
        created = self.store.create_profile("第三位收件人", "basic")
        profile = created["id"]
        self.assertRegex(profile, ui.PROFILE_ID_RE)
        raw = json.loads((self.profiles_root / profile / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["profile"]["state_file"], f"profiles/{profile}/state.json")
        self.assertEqual(raw["profile"]["output_dir"], f"profiles/{profile}/outputs")
        env_text = (self.profiles_root / profile / "credentials.env.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("top-secret", env_text)
        self.assertNotIn("full-secret", env_text)
        self.assertEqual(self.store.get(profile)["credentials"]["DEEPSEEK_API_KEY"], "")
        self.assertEqual(created["task_prefix"], f"PowerSystemAcademicRadar_{profile}")

    def test_rename_changes_only_display_name(self):
        created = self.store.create_profile("旧名称", "basic")
        renamed = self.store.rename_profile(created["id"], "新名称")
        self.assertEqual(renamed["name"], "新名称")
        self.assertEqual(renamed["id"], created["id"])
        self.assertTrue((self.profiles_root / created["id"] / "config.json").exists())

    def test_delete_disables_four_tasks_and_moves_files_to_trash(self):
        created = self.store.create_profile("待删除", "basic")
        source = self.profiles_root / created["id"]
        with mock.patch.object(ui.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            deleted = self.store.delete_profile(created["id"])
        self.assertEqual(run.call_count, 4)
        self.assertFalse(source.exists())
        trash_path = Path(deleted["trash_path"])
        self.assertTrue(trash_path.exists())
        self.assertTrue((trash_path / "config.json").exists())
        self.assertNotIn(created["id"], [row["id"] for row in self.store.list_profiles()])

    def test_at_least_one_profile_must_remain(self):
        with mock.patch.object(ui.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.store.delete_profile("full")
            with self.assertRaisesRegex(ui.ConfigError, "至少保留一个"):
                self.store.delete_profile("basic")

    def test_profile_limit_is_twenty(self):
        for index in range(ui.MAX_PROFILES - 2):
            self.store.create_profile(f"方案 {index}", "basic")
        with self.assertRaisesRegex(ui.ConfigError, "最多支持 20"):
            self.store.create_profile("超出上限", "basic")

    def test_safe_ids_and_missing_clone_are_rejected(self):
        for unsafe in ("../secret", "A", "", "a/b", "profile with space"):
            with self.subTest(profile=unsafe), self.assertRaises(ui.ConfigError):
                self.store.get(unsafe)
        with self.assertRaisesRegex(ui.ConfigError, "不存在"):
            self.store.create_profile("错误克隆", "missing")

    def test_each_created_profile_has_unique_state_output_and_task_prefix(self):
        first = self.store.create_profile("收件人甲", "basic")
        second = self.store.create_profile("收件人乙", "basic")
        first_raw = json.loads((self.profiles_root / first["id"] / "config.json").read_text(encoding="utf-8"))
        second_raw = json.loads((self.profiles_root / second["id"] / "config.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first_raw["profile"]["state_file"], second_raw["profile"]["state_file"])
        self.assertNotEqual(first_raw["profile"]["output_dir"], second_raw["profile"]["output_dir"])
        self.assertNotEqual(first["task_prefix"], second["task_prefix"])


if __name__ == "__main__":
    unittest.main()
