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


def resource_root() -> Path:
    """程序根目录：frozen 时为 exe 同级；开发时为仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="电力系统文献雷达桌面配置台")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    root = resource_root()
    sys.path.insert(0, str(root / "scripts"))
    import radar_config_ui as ui

    # 打包后资源与用户数据统一在 exe 同级目录
    ui.PLUGIN_ROOT = root
    ui.ASSET_PATH = root / "assets" / "radar_control_panel.html"

    # 计划任务需要真实 Python 解释器（frozen 时 sys.executable 是 exe 本身）
    if not os.environ.get("RADAR_PYTHON_EXE"):
        found = shutil.which("python") or shutil.which("py")
        if found:
            os.environ["RADAR_PYTHON_EXE"] = found

    server = ThreadingHTTPServer(("127.0.0.1", args.port), ui.RadarUIHandler)
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
