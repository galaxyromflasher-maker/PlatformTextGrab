"""初始化一个博主的语料项目目录。

用法：
    python init_project.py --creator-name 王羽聊教培 --creator-id 66934013427
    python init_project.py --creator-name 王羽聊教培 --creator-id 66934013427 --root D:/corpus

产物：
    <root>/<昵称>-<抖音号>/
        清单/ 转写/ 供AI查阅/
        STATUS.md
        供AI查阅/plan.json         默认方案参数，G1 确认后覆盖
        供AI查阅/quota-ledger.json 额度台账，初值取自内置额度表
        供AI查阅/README.md         文件说明
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

# 额度初值（秒）。K=1000, M=1_000_000。见 references/quota-and-models.md
QUOTA_TABLE = [
    ("paraformer-v2", "free-monthly", 36_000, 0.00008, None),
    ("paraformer-8k-v2", "free-monthly", 36_000, 0.00008, None),
    ("fun-asr", "free-once", 36_000, 0.00022, "2026-12-09"),
    ("qwen3-asr-flash-filetrans", "free-once", 36_000, 0.00022, "2026-12-09"),
    ("qwen-audio-3.0-asr-flash-filetrans", "free-once", 36_000, 0.00022, "2026-12-09"),
    ("qwen-audio-3.1-asr-flash-filetrans", "free-once", 1_000_000, 0.00022, "2026-12-21"),
]

MODEL_CHAIN = [
    "paraformer-v2",
    "fun-asr",
    "qwen3-asr-flash-filetrans",
    "qwen-audio-3.0-asr-flash-filetrans",
    "qwen-audio-3.1-asr-flash-filetrans",
]

BANNED_MODELS = ["qwen3-asr-flash"]

STATUS_TEMPLATE = """阶段：G1 准备中
待确认：登录态与方案
下一步：完成扫码并验证全量可见
更新：{now}
"""

LEDGER_README = """# 供AI查阅

本目录供 Agent 读取，人不必看。

| 文件 | 内容 | 谁读 | 什么时候读 |
|---|---|---|---|
| plan.json | G1 确认的方案参数 | Agent | G2/G3 开始前 |
| quota-ledger.json | 额度台账（逐模型已用秒数） | Agent | 每次调用前读、调用后写 |
| env-check.json | 环境体检结果 | Agent | 排查环境问题时 |
| login-state.json | 登录状态（不存 cookie） | Agent | G2 前确认是否全量可见 |
| sample-probe.json | 抽样直链与实测倍速 | Agent | G1 算耗时用 |
| manifest-raw.json | 清单原始抓取数据 | Agent | 重建 works.csv 时 |
| checks.json | 对账结果 | Agent | G2 报告引用数字时 |
| preflight-g3.json | 转写前检查 | Agent | G3 开跑前 |
| transcribe-summary.json | 转写汇总统计 | Agent | 生成 G3 报告时 |
| run-log.jsonl | 逐条执行日志 | Agent | 排查失败条目时 |
"""


def make_ledger(budget: float) -> dict:
    return {
        "budgetCny": budget,
        "spentCny": 0.0,
        "modelChain": MODEL_CHAIN,
        "bannedModels": BANNED_MODELS,
        "models": [
            {
                "name": name,
                "tier": tier,
                "freeQuotaSeconds": quota,
                "usedSeconds": 0,
                "unitPriceCnyPerSecond": price,
                "expiresAt": expires,
            }
            for name, tier, quota, price, expires in QUOTA_TABLE
        ],
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--creator-name", required=True)
    parser.add_argument("--creator-id", required=True)
    parser.add_argument("--root", default="./抖音博主文案提取")
    parser.add_argument("--budget", type=float, default=5.0)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    base = Path(args.root).resolve()
    project = base / f"{args.creator_name}-{args.creator_id}"
    if project.exists():
        print(f"已存在，不覆盖：{project}")
    for sub in ("清单", "转写", "供AI查阅"):
        (project / sub).mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")

    status = project / "STATUS.md"
    if not status.exists():
        status.write_text(STATUS_TEMPLATE.format(now=now), encoding="utf-8")

    plan_path = project / "供AI查阅" / "plan.json"
    if not plan_path.exists():
        plan_path.write_text(
            json.dumps(
                {
                    "creatorName": args.creator_name,
                    "creatorId": args.creator_id,
                    "budgetCny": args.budget,
                    "workers": args.workers,
                    "keepAudio": True,
                    "modelChain": MODEL_CHAIN,
                    "bannedModels": BANNED_MODELS,
                    "confirmedAt": None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    ledger_path = project / "供AI查阅" / "quota-ledger.json"
    if not ledger_path.exists():
        ledger_path.write_text(
            json.dumps(make_ledger(args.budget), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    readme = project / "供AI查阅" / "README.md"
    if not readme.exists():
        readme.write_text(LEDGER_README, encoding="utf-8")

    print(f"项目目录：{project}")
    print(f"预算：¥{args.budget}　并发：{args.workers}")
    print(f"模型链：{' → '.join(MODEL_CHAIN)}")
    print(f"禁用：{', '.join(BANNED_MODELS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
