#!/usr/bin/env python3
"""电力系统文献雷达 — 应用内自更新模块。

从 GitHub Releases 检查新版本、下载升级包并完成"退出后自动替换文件再重启"的
一站式升级。设计要点：

- 只使用标准库（urllib/zipfile/subprocess），不引入第三方依赖；
- Windows 下运行中的 exe 无法覆盖自己，因此真正的文件替换由本模块生成的
  ``update_self.ps1`` 在控制台进程退出后执行（等待 PID → 备份 → 替换 → 重启）；
- 用户数据（profiles/、work/、logs/、radar.env.ps1、credentials.env.ps1 等）
  永远不会被升级脚本触碰；备份与暂存目录都放在 work/ 之下，同样受保护。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

# 发布版本号：与 git tag（如 v0.3.5）保持一致，发布新版本时同步修改这里。
APP_VERSION = "0.3.5"

GITHUB_REPO = "hfk369258/power-system-academic-radar"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ASSET_PREFIX = "power-system-radar-ui_v"

# 升级时需要替换的程序文件/目录（存在于升级包中才会替换）。
PROGRAM_ITEMS = (
    "power-system-radar-ui.exe",
    "_internal",
    "scripts",
    "assets",
    "skills",
    "docs",
    "radar.env.example.ps1",
)

USER_AGENT = f"power-system-radar-ui/{APP_VERSION} (self-update)"


class UpdateError(Exception):
    """升级流程中的可读错误，message 面向用户（中文）。"""


# ---------------------------------------------------------------------------
# 版本比较与 Release 查询
# ---------------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """把 v0.3.4 / 0.3.4-1-gabc 解析成可比较的整数元组。"""
    match = re.search(r"\d+(?:\.\d+)*", str(text or ""))
    if not match:
        return (0,)
    return tuple(int(part) for part in match.group(0).split("."))


def version_string(tag: str) -> str:
    """v0.3.4 -> 0.3.4，仅用于展示。"""
    return str(tag or "").lstrip("vV").split("-", 1)[0]


def fetch_latest_release(timeout: float = 15.0) -> dict[str, Any]:
    """查询 GitHub 最新 Release 及其升级 zip 资产。"""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — 统一转成用户可读错误
        raise UpdateError(
            f"无法连接 GitHub 查询最新版本（{exc}）；请检查网络后重试"
        ) from exc
    if not isinstance(data, dict) or not data.get("tag_name"):
        raise UpdateError("GitHub 返回的版本信息格式异常，请稍后重试")
    asset = None
    for item in data.get("assets") or []:
        name = str(item.get("name") or "")
        if name.startswith(ASSET_PREFIX) and name.endswith(".zip"):
            asset = item
            break
    if asset is None:
        raise UpdateError("最新 Release 中没有找到升级压缩包，请联系维护者检查发布产物")
    return {
        "tag": data["tag_name"],
        "name": data.get("name") or data["tag_name"],
        "notes": (data.get("body") or "").strip()[:2000],
        "published_at": data.get("published_at") or "",
        "zip_name": asset.get("name") or "",
        "zip_url": asset.get("browser_download_url") or "",
        "zip_size": int(asset.get("size") or 0),
    }


def check_update(current: str = APP_VERSION, timeout: float = 15.0) -> dict[str, Any]:
    """对比本地与远端版本，返回前端展示所需的完整信息。"""
    latest = fetch_latest_release(timeout=timeout)
    has_update = parse_version(latest["tag"]) > parse_version(current)
    return {
        "current_version": current,
        "latest_version": version_string(latest["tag"]),
        "has_update": has_update,
        **latest,
    }


# ---------------------------------------------------------------------------
# 部署目录定位
# ---------------------------------------------------------------------------

def deployment_dirs() -> tuple[Path, Path, str]:
    """返回 (data_root, target_dir, exe_name)。

    与 radar_app.data_root_for 的规则保持一致：exe 位于仓库 dist 布局内时，
    数据根目录是仓库根；独立拷贝的 exe 则用 exe 同级目录。
    开发模式（非 frozen）不支持自更新。
    """
    if not getattr(sys, "frozen", False):
        raise UpdateError("开发模式不支持一键升级，请用 git pull 更新代码")
    exe_path = Path(sys.executable).resolve()
    target = exe_path.parent
    candidate = target.parent.parent
    data_root = (
        candidate if (candidate / "scripts" / "power_system_radar.py").exists() else target
    )
    return data_root, target, exe_path.name


# ---------------------------------------------------------------------------
# 下载与暂存
# ---------------------------------------------------------------------------

def stage_download(
    zip_url: str,
    work_root: Path,
    timeout: float = 900.0,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """下载升级 zip 并解压到 work/update_staging/payload，返回解压后的程序根目录。"""
    if not zip_url:
        raise UpdateError("升级包下载地址为空")
    staging = work_root / "update_staging"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    payload_root = staging / "payload"
    payload_root.mkdir(parents=True, exist_ok=True)
    zip_path = staging / "release.zip"

    request = urllib.request.Request(zip_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp, zip_path.open("wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"下载升级包失败（{exc}）；请检查网络后重试") from exc

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(payload_root)
    except zipfile.BadZipFile as exc:
        raise UpdateError("下载的升级包损坏（不是有效的 zip），请重试") from exc

    root = find_payload_root(payload_root)
    missing = [
        item
        for item in ("power-system-radar-ui.exe", "_internal")
        if not (root / item).exists()
    ]
    if missing:
        raise UpdateError(f"升级包内容不完整（缺少 {missing[0]}），已取消升级")
    return root


def find_payload_root(extract_dir: Path) -> Path:
    """兼容两种 zip 布局：带单层顶层目录 / 直接平铺。"""
    entries = list(extract_dir.iterdir())
    dirs = [item for item in entries if item.is_dir()]
    files = [item for item in entries if item.is_file()]
    if len(dirs) == 1 and not files and (dirs[0] / "power-system-radar-ui.exe").exists():
        return dirs[0]
    return extract_dir


# ---------------------------------------------------------------------------
# 升级脚本生成与执行
# ---------------------------------------------------------------------------

UPDATER_TEMPLATE = r"""# 由文献雷达自动生成的自更新脚本（请勿手工编辑）
$ErrorActionPreference = 'Stop'
$Target   = '{target}'
$Payload  = '{payload}'
$ExeName  = '{exe_name}'
$OldPid   = {pid}
$Log      = '{log}'
$Items    = @({items})

function Write-Log([string]$Message) {{
    try {{ "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Out-File -FilePath $Log -Append -Encoding utf8 }} catch {{ }}
}}

function Move-ProgramItems([string]$FromDir, [string]$ToDir) {{
    foreach ($item in $Items) {{
        $src = Join-Path $FromDir $item
        if (Test-Path -LiteralPath $src) {{
            New-Item -ItemType Directory -Force -Path $ToDir | Out-Null
            Move-Item -LiteralPath $src -Destination (Join-Path $ToDir $item) -Force
        }}
    }}
}}

Write-Log "升级开始：等待旧进程退出 PID=$OldPid"
$backup = ''
try {{
    if ($OldPid -gt 0) {{
        try {{ Wait-Process -Id $OldPid -Timeout 90 -ErrorAction Stop }} catch {{ Write-Log "旧进程已退出或等待超时" }}
    }}
    Start-Sleep -Seconds 2

    $backup = Join-Path $Target ("work\update_backup\" + (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Move-ProgramItems $Target $backup
    Write-Log "旧程序文件已备份到 $backup"

    foreach ($item in $Items) {{
        $new = Join-Path $Payload $item
        if (Test-Path -LiteralPath $new) {{
            Copy-Item -LiteralPath $new -Destination (Join-Path $Target $item) -Recurse -Force
        }}
    }}
    Write-Log "新程序文件已就位，重启控制台"

    Start-Process -FilePath (Join-Path $Target $ExeName) -WorkingDirectory $Target
    Write-Log "升级完成"

    # 清理 30 天前的历史备份
    Get-ChildItem (Join-Path $Target 'work\update_backup') -Directory -ErrorAction SilentlyContinue |
        Where-Object {{ $_.LastWriteTime -lt (Get-Date).AddDays(-30) }} |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Write-Log ("升级失败：" + $_.Exception.Message + "；尝试回滚")
    if ($backup -and (Test-Path -LiteralPath $backup)) {{
        try {{
            Move-ProgramItems $backup $Target
            Write-Log "已回滚到旧版本"
        }} catch {{
            Write-Log ("回滚也失败：" + $_.Exception.Message)
        }}
    }}
    exit 1
}}
"""


def write_updater_script(
    staging_dir: Path,
    payload_root: Path,
    target_dir: Path,
    exe_name: str,
    pid: int,
    log_path: Path,
) -> Path:
    """生成 update_self.ps1，返回脚本路径。"""
    items = ", ".join(f"'{item}'" for item in PROGRAM_ITEMS)
    body = UPDATER_TEMPLATE.format(
        target=str(target_dir),
        payload=str(payload_root),
        exe_name=exe_name,
        pid=int(pid),
        log=str(log_path),
        items=items,
    )
    script_path = staging_dir / "update_self.ps1"
    script_path.write_text(body, encoding="utf-8-sig")
    return script_path


def launch_updater(script_path: Path, work_dir: Path) -> None:
    """以分离进程启动升级脚本，使其在主程序退出后继续运行。"""
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    log_fh = (work_dir / "update_launcher.log").open("ab")
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            creationflags=flags,
            cwd=str(work_dir),
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
    finally:
        log_fh.close()


# ---------------------------------------------------------------------------
# 后台升级任务管理（供 HTTP 接口调用）
# ---------------------------------------------------------------------------

class UpdateManager:
    """串行化的一次性升级任务：check 在请求线程同步执行，apply 走后台线程。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"phase": "idle", "message": "", "progress": None}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _set(self, phase: str, message: str = "", progress: float | None = None) -> None:
        with self._lock:
            self._status = {"phase": phase, "message": message, "progress": progress}

    def start(self, tag: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self._status.get("phase") not in {"idle", "error", "done"}:
                raise UpdateError("已有升级任务正在进行，请等待其完成")
        data_root, target, exe_name = deployment_dirs()
        work_root = data_root / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        worker = threading.Thread(
            target=self._run,
            args=(work_root, target, exe_name, tag),
            daemon=True,
            name="radar-self-update",
        )
        worker.start()
        return {"started": True}

    def _run(self, work_root: Path, target: Path, exe_name: str, tag: str | None) -> None:
        try:
            self._set("checking", "正在查询最新版本…")
            info = fetch_latest_release()
            if tag and version_string(info["tag"]) != version_string(tag):
                raise UpdateError(
                    f"远端最新版本已是 {info['tag']}，与页面提示的 {tag} 不一致，请重新检查更新"
                )

            def on_progress(done: int, total: int) -> None:
                percent = round(done * 100 / total, 1) if total else None
                self._set(
                    "downloading",
                    f"正在下载升级包… {done // 1048576}/{max(total // 1048576, 1)} MB",
                    percent,
                )

            self._set("downloading", "正在下载升级包…")
            payload = stage_download(info["zip_url"], work_root, progress=on_progress)

            self._set("staging", "正在准备升级脚本…")
            staging = work_root / "update_staging"
            log_path = work_root / "update.log"
            script = write_updater_script(
                staging_dir=staging,
                payload_root=payload,
                target_dir=target,
                exe_name=exe_name,
                pid=os.getpid(),
                log_path=log_path,
            )
            launch_updater(script, work_root)

            self._set("restarting", "升级包已就绪，控制台即将关闭并自动重启…")
            shutdown_timer = threading.Timer(1.5, os._exit, args=(0,))
            shutdown_timer.daemon = True
            shutdown_timer.start()
        except UpdateError as exc:
            self._set("error", str(exc))
        except Exception as exc:  # noqa: BLE001 — 兜底，避免线程静默死亡
            self._set("error", f"升级失败：{exc}")


def cleanup_staging(work_root: Path) -> None:
    """删除残留的升级暂存目录（正常流程中由下次升级前清理兜底）。"""
    shutil.rmtree(work_root / "update_staging", ignore_errors=True)
