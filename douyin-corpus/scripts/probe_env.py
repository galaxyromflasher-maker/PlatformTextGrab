"""环境体检：Python 版本、依赖包、ffmpeg、磁盘、网络。

用法：
    python probe_env.py
    python probe_env.py --project <博主目录>   # 结果同时写入 供AI查阅/env-check.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def check_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_binary(name: str) -> str | None:
    path = shutil.which(name)
    if not path:
        return None
    try:
        result = subprocess.run([name, "-version"], capture_output=True, text=True, timeout=10)
        return result.stdout.splitlines()[0][:80] if result.stdout else path
    except Exception:  # noqa: BLE001
        return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    parser.add_argument("--disk-path", default=None, help="检查哪个盘的剩余空间，默认当前目录")
    args = parser.parse_args()

    # 磁盘要按项目所在盘算：在 C 盘执行、项目在 D 盘时，用 cwd 会误报不足
    disk_target = Path(args.disk_path or args.project or Path.cwd()).expanduser()
    while not disk_target.exists() and disk_target != disk_target.parent:
        disk_target = disk_target.parent

    report = {
        "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "pythonOk": sys.version_info >= (3, 10),
        "modules": {m: check_module(m) for m in ("requests", "openpyxl")},
        "ffmpeg": bool(check_binary("ffmpeg")),
        "ffprobe": bool(check_binary("ffprobe")),
        "playwright": check_module("playwright"),
        "diskPath": str(disk_target),
        "diskFreeGB": round(shutil.disk_usage(disk_target).free / 2**30, 1),
    }
    missing = [m for m, ok in report["modules"].items() if not ok]
    report["ok"] = report["pythonOk"] and not missing and report["ffmpeg"] and report["ffprobe"]
    report["hints"] = []
    if not report["pythonOk"]:
        report["hints"].append("Python 需 3.10+（Windows 上可能是商店占位别名，见 references/setup-windows.md）")
    if missing:
        report["hints"].append(f"缺少依赖：{', '.join(missing)} → pip install {' '.join(missing)}")
    if not report["ffmpeg"]:
        report["hints"].append("未找到 ffmpeg → 见 references/setup-windows.md 第 3 节（npmmirror 镜像，10 秒装完）")
    if not report["ffprobe"]:
        report["hints"].append("未找到 ffprobe → 同上，ffmpeg 和 ffprobe 两个都要")
    if not report.get("playwright"):
        report["hints"].append("缺少 playwright → pip install playwright（无需 install chromium）")
    if report["diskFreeGB"] < 5:
        report["hints"].append(f"磁盘仅剩 {report['diskFreeGB']} GB，200 条音频约需 1.5 GB")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.project:
        out = Path(args.project).resolve() / "供AI查阅" / "env-check.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
