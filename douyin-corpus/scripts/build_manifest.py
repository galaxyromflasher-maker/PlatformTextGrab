"""从浏览器抓取的原始 JSON 生成清单表格、对账结果与 G2 报告。

用法：
    python build_manifest.py --project <博主目录> --raw raw.json --page-total 200
    python build_manifest.py --project <博主目录> --raw raw.json --page-total 200 --rules rules.json

raw.json 结构（由 assets/capture.js 产出）：
    {"profileUrl": "...", "nickname": "...", "secUid": "...", "tabTotal": "200",
     "items": [{"id","desc","createTime","duration","isTop","digg","comment","collect","share","imageCount","playUrl"}]}

rules.json（可选，用于标题粗分类）：
    [{"name": "招生与获客", "pattern": "招生|获客|拉新"}]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
FIELDS = ["类别", "序号", "作品ID", "标题", "发布日期", "时长分钟", "点赞数", "评论数", "收藏数", "转发数", "类型"]


def load_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    while isinstance(data, str):
        data = json.loads(data)
    return data


def normalize_total(raw) -> int | None:
    """页面总数可能是 1355、'1355'、'1.4万'、'1.4w'，统一成整数。"""
    if raw is None:
        return None
    text = str(raw).strip()
    m = re.search(r"([\d.]+)\s*([万wW]?)", text)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2):
        value *= 10000
    return int(value)


def classify(title: str, rules: list[dict]) -> str:
    for rule in rules:
        if re.search(rule["pattern"], title, re.I):
            return rule["name"]
    return ""


def forecast(seconds: float, ledger: dict) -> dict:
    """按模型链顺序切分，留 5% 余量，算出预计花费。"""
    remaining = seconds
    cost = 0.0
    plan = []
    for name in ledger.get("modelChain", []):
        model = next((m for m in ledger["models"] if m["name"] == name), None)
        if not model:
            continue
        free_left = max(0, model["freeQuotaSeconds"] - model["usedSeconds"])
        usable = free_left * 0.95
        take = min(remaining, usable)
        if take <= 0:
            continue
        # take 全部在免费额度内（take <= usable < free_left），不产生超额费用
        plan.append({"model": name, "seconds": round(take), "overageCny": 0.0})
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        # 免费额度全部用完，剩余部分需要付费（按最便宜的模型单价估算）
        cheapest = min(m["unitPriceCnyPerSecond"] for m in ledger["models"] if m.get("unitPriceCnyPerSecond"))
        overage = remaining * cheapest
        cost += overage
        plan.append({"model": "（免费额度不足，需付费）", "seconds": round(remaining), "overageCny": round(overage, 2)})
    return {"plan": plan, "costCny": round(cost, 2), "shortfallSeconds": round(max(0.0, remaining))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--page-total", default=None)
    parser.add_argument("--rules", default=None)
    parser.add_argument("--raw2", default=None, help="第二次抓取的原始 JSON，用于差异诊断")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    raw = load_json(Path(args.raw))
    rules = load_json(Path(args.rules)) if args.rules else []
    items = raw.get("items") or []

    seen: dict[str, dict] = {}
    dupes = []
    for index, item in enumerate(items, start=1):
        wid = str(item.get("id") or "")
        if not wid:
            continue
        if wid in seen:
            dupes.append(wid)
            continue
        created = item.get("createTime")
        date = datetime.fromtimestamp(int(created), tz=CST).date().isoformat() if created else ""
        kind = "图文" if (item.get("imageCount") or 0) > 0 else "视频"
        seen[wid] = {
            "类别": classify(item.get("desc") or "", rules),
            "序号": f"{index:03d}",
            "作品ID": wid,
            "标题": (item.get("desc") or "").replace("\n", " ").strip(),
            "发布日期": date,
            "时长分钟": round((item.get("duration") or 0) / 60000, 2),
            "点赞数": item.get("digg") or 0,
            "评论数": item.get("comment") or 0,
            "收藏数": item.get("collect") or 0,
            "转发数": item.get("share") or 0,
            "类型": kind,
        }

    rows = sorted(seen.values(), key=lambda r: r["发布日期"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["序号"] = f"{index:03d}"

    out_dir = project / "清单"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "works.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter

        widths = [12, 6, 21, 60, 12, 10, 9, 9, 9, 9, 8]
        wb = Workbook()
        ws = wb.active
        ws.title = "清单"
        ws.append(FIELDS)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for row in rows:
            ws.append([row[f] for f in FIELDS])
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        wb.save(out_dir / "works.xlsx")
    except ImportError:
        print("未安装 openpyxl，跳过 xlsx")

    total_seconds = sum(int(float(r["时长分钟"]) * 60) for r in rows)
    videos = sum(1 for r in rows if r["类型"] == "视频")
    images = len(rows) - videos
    dates = [r["发布日期"] for r in rows if r["发布日期"]]
    page_total = normalize_total(args.page_total or raw.get("tabTotal"))

    # 差异诊断：连抓两次比对 ID 集合。两次一致说明不是加载遗漏，
    # 而是主页计数里含私密/审核中/已下架的作品。
    diff2 = None
    if args.raw2:
        raw2 = load_json(Path(args.raw2))
        ids2 = {str(i.get("id")) for i in (raw2.get("items") or []) if i.get("id")}
        only_first = sorted(set(seen) - ids2)
        only_second = sorted(ids2 - set(seen))
        diff2 = {
            "第二次抓取条数": len(ids2),
            "仅第一次有": len(only_first),
            "仅第二次有": len(only_second),
            "样例": (only_first[:5] + only_second[:5]),
            "结论": "两次集合一致，差异来自页面计数含非公开作品" if not only_first and not only_second else "两次集合不一致，可能存在加载遗漏",
        }

    ledger_path = project / "供AI查阅" / "quota-ledger.json"
    ledger = load_json(ledger_path) if ledger_path.exists() else {"modelChain": [], "models": []}
    # 只统计视频（图文无音轨）
    audio_seconds = sum(int(float(r["时长分钟"]) * 60) for r in rows if r["类型"] == "视频")
    plan = forecast(audio_seconds, ledger)

    zero_dur = [r["序号"] for r in rows if r["类型"] == "视频" and float(r["时长分钟"]) == 0]
    checks = {
        "页面显示总数": page_total,
        "抓取条数": len(items),
        "去重后条数": len(rows),
        "视频": videos,
        "图文": images,
        "重复ID数": len(dupes),
        "重复ID样例": dupes[:10],
        "时间跨度": [dates[-1] if dates else "", dates[0] if dates else ""],
        "音视频总秒数": total_seconds,
        "视频总秒数": audio_seconds,
        "音视频总小时": round(total_seconds / 3600, 2),
        "对账结论": "一致" if page_total == len(rows) else "不一致",
        "差异条数": (page_total - len(rows)) if page_total else None,
        "差异诊断": diff2,
        "额度分配": plan["plan"],
        "预计费用CNY": plan["costCny"],
        "额度缺口秒数": plan["shortfallSeconds"],
        "时长未知条目": zero_dur,
        "时长未知说明": "duration=0 的条目，可能是图文/音乐作品，转写结果可能为空" if zero_dur else "",
        "生成时间": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    (project / "供AI查阅" / "checks.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (project / "供AI查阅" / "manifest-raw.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )

    plan_path = project / "供AI查阅" / "plan.json"
    if plan_path.exists():
        data = load_json(plan_path)
        data["mediaSeconds"] = audio_seconds
        data["budgetForecastCny"] = plan["costCny"]
        plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    plan_text = "；".join(
        "{} {} 秒".format(p["model"], p["seconds"]) for p in plan["plan"]
    ) or "无可用模型"
    cost_note = "预计 ¥0（额度内）" if plan["costCny"] == 0 else f"预计 ¥{plan['costCny']}"
    md = f"""# G2 清单 · {raw.get('nickname') or project.name}

结论：{len(rows)} 条作品，与页面显示{('一致' if checks['对账结论'] == '一致' else '**不一致**')}，{cost_note}。

## 范围
{len(rows)} 条（视频 {videos} / 图文 {images}），{dates[-1] if dates else ''} 至 {dates[0] if dates else ''}，视频合计 {audio_seconds/3600:.2f} 小时。

## 依据
- 页面显示总数 {page_total}，抓取 {len(items)} 条，去重后 {len(rows)} 条
- 重复 ID {len(dupes)} 个
- 额度分配：{plan_text}
- 预计费用 ¥{plan['costCny']}

## 风险
- 图文 {images} 条无音轨，本轮不转写
- 若额度缺口 > 0，需追加预算或分批

## 已排除
- 不做内容分析、不做纠错
- 不下载视频原片，仅保留音轨

## 下一步
回复「确认」开始转写
"""
    (project / "G2-清单.md").write_text(md, encoding="utf-8")

    status = project / "STATUS.md"
    status.write_text(
        f"""阶段：G2 待确认
待确认：清单与额度分配
下一步：回复「确认」开始转写
更新：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}
""",
        encoding="utf-8",
    )

    print(f"清单：{out_dir / 'works.csv'}")
    print(f"条数：{len(rows)}（视频 {videos} / 图文 {images}）　总时长 {total_seconds/3600:.2f} 小时")
    print(f"对账：{checks['对账结论']}（页面 {page_total} / 去重 {len(rows)}）")
    print("额度：" + plan_text)
    print(f"预计费用：¥{plan['costCny']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
