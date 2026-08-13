#!/usr/bin/env python3
"""电力系统文献雷达 — 桌面配置台入口。

开发模式：`python scripts/radar_app.py`（打开默认浏览器）
打包模式：PyInstaller onedir 构建后，用户数据（profiles/、radar.env.ps1、outputs/）与
scripts/、assets/ 均位于 exe 同级目录，与仓库布局一致；窗口使用系统 WebView2（无需浏览器）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any


def resource_root() -> Path:
    """程序根目录：frozen 时为 exe 同级；开发时为仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def redirect_console_to_log(root: Path) -> None:
    """无控制台窗口（--noconsole）模式下把 stdout/stderr 落盘，便于排障。

    打包成 GUI 子系统后没有终端窗口，print 输出全部追加写入
    logs/console-ui.log，与雷达运行日志（logs/radar_*.log）分开。
    """
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "console-ui.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def data_root_for(root: Path) -> Path:
    """确定用户数据根目录。

    当 exe 部署在仓库的 dist/power-system-radar-ui 布局内时，方案、凭据、日志与
    定时任务统一使用仓库根目录（与每日计划任务完全一致）；独立拷贝的 exe 则继续
    使用 exe 同级目录。
    """
    if getattr(sys, "frozen", False):
        candidate = root.parent.parent
        if (candidate / "scripts" / "power_system_radar.py").exists():
            return candidate
    return root


def configure_ui_data_root(ui: Any, data_root: Path) -> None:
    """让控制台后端的所有数据路径指向同一个根目录，并重建 ConfigStore。

    必须在任何 store 访问之前调用：模块级常量（PROFILES_ROOT 等）在 import 时已按
    exe 目录计算，ConfigStore 实例也在类定义时创建，因此这里覆盖常量后重建 store。
    """
    ui.PLUGIN_ROOT = data_root
    ui.ASSET_PATH = data_root / "assets" / "radar_control_panel.html"
    ui.PROFILES_ROOT = data_root / "profiles"
    ui.PROFILES_REGISTRY = ui.PROFILES_ROOT / "profiles.json"
    ui.PROFILE_FILES = {
        "basic": data_root / "assets" / "power_system_radar_config.json",
        "full": data_root / "assets" / "power_system_radar_config_full.json",
    }
    ui.ENV_FILES = {
        "basic": Path(os.environ.get("RADAR_UI_BASIC_ENV", str(data_root / "radar.env.ps1"))),
        "full": Path(os.environ.get("RADAR_UI_FULL_ENV", str(data_root / "radar.env_full.ps1"))),
    }
    ui.RadarUIHandler.store = ui.ConfigStore()


def main() -> int:
    parser = argparse.ArgumentParser(description="电力系统文献雷达桌面配置台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    root = resource_root()
    data_root = data_root_for(root)
    if getattr(sys, "frozen", False):
        # GUI 子系统没有终端，控制台日志写入 logs/console-ui.log
        redirect_console_to_log(data_root)
    sys.path.insert(0, str(root / "scripts"))
    import radar_config_ui as ui

    # 控制台数据与定时任务共用同一个根目录（方案/凭据/日志/资产）
    configure_ui_data_root(ui, data_root)

    # 计划任务需要真实 Python 解释器（frozen 时 sys.executable 是 exe 本身）
    if not os.environ.get("RADAR_PYTHON_EXE"):
        found = shutil.which("python") or shutil.which("py")
        if found:
            os.environ["RADAR_PYTHON_EXE"] = found

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), ui.RadarUIHandler)
    except OSError as exc:
        message = (
            f"无法启动控制台：端口 {args.port} 已被占用。\n"
            "可能已有旧的文献雷达控制台在运行。\n"
            "请关闭旧窗口后重试，或用 --port 指定其它端口。\n\n"
            f"（系统信息：{exc}）"
        )
        print(message, file=sys.stderr)
        if getattr(sys, "frozen", False):
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, message, "电力系统文献雷达控制台", 0x10)  # MB_ICONERROR
            except Exception:  # noqa: BLE001 — 弹窗失败也不影响日志落盘
                pass
        raise SystemExit(2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{args.port}/"
    print(f"文献雷达控制台：{url}")

    try:
        import webview  # type: ignore[import-not-found]

        webview.create_window("电力系统文献雷达配置台", url, width=1280, height=880)
        webview.start()
    except Exception:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
