import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import radar_update as ru


def _fake_github_payload(tag="v0.4.0", asset_name="power-system-radar-ui_v0.4.0.zip"):
    return {
        "tag_name": tag,
        "name": f"Release {tag}",
        "body": "修复若干问题",
        "published_at": "2026-08-21T00:00:00Z",
        "assets": [
            {"name": asset_name, "browser_download_url": f"https://example.com/{asset_name}", "size": 12345}
        ],
    }


class FakeResponse:
    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ParseVersionTest(unittest.TestCase):
    def test_ordering(self):
        self.assertLess(ru.parse_version("v0.3.4"), ru.parse_version("v0.3.10"))
        self.assertLess(ru.parse_version("0.9.9"), ru.parse_version("1.0.0"))
        self.assertEqual(ru.parse_version("v0.3.4-1-gabc"), ru.parse_version("0.3.4"))
        self.assertEqual(ru.parse_version(""), (0,))

    def test_version_string(self):
        self.assertEqual(ru.version_string("v0.3.4"), "0.3.4")
        self.assertEqual(ru.version_string("0.3.5-rc1"), "0.3.5")


class FetchLatestReleaseTest(unittest.TestCase):
    def test_picks_zip_asset(self):
        payload = json.dumps(_fake_github_payload()).encode()
        with mock.patch.object(ru.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            info = ru.fetch_latest_release()
        self.assertEqual(info["tag"], "v0.4.0")
        self.assertEqual(info["zip_url"], "https://example.com/power-system-radar-ui_v0.4.0.zip")
        self.assertEqual(info["zip_size"], 12345)

    def test_missing_asset_raises(self):
        payload = json.dumps({"tag_name": "v0.4.0", "assets": [{"name": "other.txt"}]}).encode()
        with mock.patch.object(ru.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            with self.assertRaises(ru.UpdateError):
                ru.fetch_latest_release()

    def test_network_error_raises_readable(self):
        with mock.patch.object(
            ru.urllib.request, "urlopen", side_effect=OSError("connection refused")
        ):
            with self.assertRaises(ru.UpdateError) as ctx:
                ru.fetch_latest_release()
        self.assertIn("无法连接 GitHub", str(ctx.exception))


class CheckUpdateTest(unittest.TestCase):
    @staticmethod
    def _release_info(tag: str) -> dict:
        return {
            "tag": tag,
            "name": f"Release {tag}",
            "notes": "",
            "published_at": "",
            "zip_name": "power-system-radar-ui_v9.9.9.zip",
            "zip_url": "https://example.com/release.zip",
            "zip_size": 1,
        }

    def test_has_update_true(self):
        with mock.patch.object(ru, "fetch_latest_release", return_value=self._release_info("v9.9.9")):
            result = ru.check_update(current="0.3.4")
        self.assertTrue(result["has_update"])
        self.assertEqual(result["current_version"], "0.3.4")

    def test_has_update_false_for_equal_and_older(self):
        for tag in ("v0.3.4", "v0.3.3"):
            with mock.patch.object(ru, "fetch_latest_release", return_value=self._release_info(tag)):
                self.assertFalse(ru.check_update(current="0.3.4")["has_update"])


class StageDownloadTest(unittest.TestCase):
    def _build_zip_bytes(self, layout="nested") -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            exe = "power-system-radar-ui.exe"
            internal = "_internal/x.dll"
            script = "scripts/run.ps1"
            if layout == "nested":
                prefix = "power-system-radar-ui"
                archive.writestr(f"{prefix}/{exe}", "MZ")
                archive.writestr(f"{prefix}/{internal}", "dll")
                archive.writestr(f"{prefix}/{script}", "ok")
            else:
                archive.writestr(exe, "MZ")
                archive.writestr(internal, "dll")
                archive.writestr(script, "ok")
        return buffer.getvalue()

    def _stage(self, data: bytes) -> Path:
        work_root = Path(tempfile.mkdtemp())
        with mock.patch.object(
            ru.urllib.request, "urlopen", return_value=FakeResponse(data)
        ):
            return ru.stage_download("https://example.com/release.zip", work_root)

    def test_nested_layout(self):
        root = self._stage(self._build_zip_bytes("nested"))
        self.assertEqual(root.name, "power-system-radar-ui")
        self.assertTrue((root / "power-system-radar-ui.exe").exists())
        self.assertTrue((root / "_internal" / "x.dll").exists())

    def test_flat_layout(self):
        root = self._stage(self._build_zip_bytes("flat"))
        self.assertTrue((root / "power-system-radar-ui.exe").exists())

    def test_incomplete_package_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "empty")
        with self.assertRaises(ru.UpdateError):
            self._stage(buffer.getvalue())


class UpdaterScriptTest(unittest.TestCase):
    def test_script_contains_key_steps(self):
        staging = Path(tempfile.mkdtemp())
        log = staging / "update.log"
        script = ru.write_updater_script(
            staging_dir=staging,
            payload_root=Path(r"D:\deploy\work\update_staging\payload"),
            target_dir=Path(r"D:\deploy"),
            exe_name="power-system-radar-ui.exe",
            pid=4321,
            log_path=log,
        )
        text = script.read_text(encoding="utf-8-sig")
        self.assertIn("$OldPid   = 4321", text)
        self.assertIn("Wait-Process -Id $OldPid", text)
        self.assertIn("update_backup", text)
        self.assertIn("Start-Process", text)
        self.assertIn("'radar.env.example.ps1'", text)
        # 用户数据目录绝不能出现在替换清单里
        for protected in ("'profiles'", "'logs'", "'work'", "'credentials.env.ps1'", "'radar.env.ps1'"):
            self.assertNotIn(protected, text)
        # PowerShell 语法校验（仅 Windows 本机执行时）
        try:
            import subprocess

            probe = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$t=$null;$e=$null;"
                        f"[System.Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$t,[ref]$e)|Out-Null;"
                        "if($e.Count){$e|ForEach-Object{$_.Message};exit 1}else{exit 0}"
                    ),
                ],
                capture_output=True,
                text=True,
            )
        except OSError:
            return
        self.assertEqual(probe.returncode, 0, f"生成的升级脚本语法错误：{probe.stdout}{probe.stderr}")


if __name__ == "__main__":
    unittest.main()
