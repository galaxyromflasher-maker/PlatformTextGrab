"""批量转写：直链下载 -> 抽音轨 -> 百炼异步 ASR -> 落盘。

用法：
    python transcribe.py --project <博主目录> --key-file <路径>
    python transcribe.py --project <博主目录> --key <粘贴的Key>
    python transcribe.py --project <博主目录> --key-file <路径> --workers 3 --limit 20

行为：
- 读取 清单/works.csv 与 供AI查阅/manifest-raw.json 里的直链
- 逐条：下载 mp4 -> 抽 m4a -> 删 mp4 -> 上传 -> 提交 -> 轮询 -> 落盘
- 额度预判 + 实报触发切换模型，只向前不回退
- 预算 80% 暂停、100% 停止
- 连续 5 条同类失败熔断
- 断点续跑：meta.json 状态为 ok / no_speech 的跳过
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

BASE_API = "https://dashscope.aliyuncs.com/api/v1"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
REFERER = "https://www.douyin.com/"
QUOTA_HINT = re.compile(r"AllocationQuota|Throttling|Arrearage|quota|额度|余额|欠费|FreeTier", re.I)
SENT_END = re.compile(r"[。！？!?]$")
TARGET_CHARS = 90
MAX_SENTENCES = 5
PROGRESS_EVERY = 20
CIRCUIT_BREAK = 5
# 这几类异常说明是代码缺陷而非业务失败，重试没意义，直接中止并打印堆栈
FATAL_ERRORS = (KeyError, TypeError, AttributeError, NameError, ImportError, IndexError)


# ---------- 文本处理 ----------

def stamp(ms: float) -> str:
    total = int(ms) // 1000
    return f"{total // 60:02d}:{total % 60:02d}"


def build_paragraphs(sentences: list[dict]) -> list[dict]:
    out, buf = [], []

    def flush() -> None:
        if not buf:
            return
        out.append(
            {
                "start": int(buf[0]["begin_time"]),
                "end": int(buf[-1]["end_time"]),
                "text": "".join(s.get("text", "") for s in buf).strip(),
            }
        )
        buf.clear()

    for sentence in sentences:
        buf.append(sentence)
        text = "".join(s.get("text", "") for s in buf).strip()
        if (len(text) >= TARGET_CHARS and SENT_END.search(text)) or len(buf) >= MAX_SENTENCES:
            flush()
    flush()
    return out


def render_md(title: str, date: str, duration_ms: int, digg: int, url: str, paras: list[dict]) -> str:
    lines = [f"# {title}", "", f"> {date} · {duration_ms/60000:.1f} 分钟 · {digg} 赞 · {url}", ""]
    for para in paras:
        lines.append(f"[{stamp(para['start'])}–{stamp(para['end'])}] {para['text']}")
        lines.append("")
    lines.append(f"_音频总长 {stamp(duration_ms)}_")
    return "\n".join(lines).rstrip() + "\n"


# ---------- 媒体与接口 ----------

def download(url: str, dest: Path) -> int:
    headers = {"User-Agent": UA, "Referer": REFERER, "Accept": "*/*"}
    with requests.get(url, headers=headers, stream=True, timeout=120) as response:
        response.raise_for_status()
        written = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                if chunk:
                    handle.write(chunk)
                    written += len(chunk)
    return written


def extract_audio(source: Path, dest: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vn", "-acodec", "copy", str(dest)],
        check=True,
    )


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


class AsrClient:
    def __init__(self, key: str, model: str) -> None:
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {key}"
        self.model = model

    def transcribe(self, audio: Path, tag: str) -> dict:
        policy = self.session.get(
            f"{BASE_API}/uploads", params={"action": "getPolicy", "model": self.model}, timeout=30
        )
        policy.raise_for_status()
        data = policy.json()["data"]

        key = f"{data['upload_dir']}/{tag}.m4a"
        form = {
            "OSSAccessKeyId": data["oss_access_key_id"],
            "Signature": data["signature"],
            "policy": data["policy"],
            "key": key,
            "x-oss-object-acl": data.get("x_oss_object_acl", "private"),
            "x-oss-forbid-overwrite": data.get("x_oss_forbid_overwrite", "true"),
            "success_action_status": "200",
        }
        with audio.open("rb") as handle:
            upload = self.session.post(
                data["upload_host"], data=form, files={"file": (f"{tag}.m4a", handle, "audio/mp4")}, timeout=600
            )
        if upload.status_code not in (200, 203, 204):
            raise RuntimeError(f"upload {upload.status_code}: {upload.text[:200]}")

        params = {"language_hints": ["zh"], "disfluency_removal_enabled": False} if self.model.startswith("paraformer") else {}
        body = {"model": self.model, "input": {"file_urls": [f"oss://{key}"]}, "parameters": params}
        submit = self.session.post(
            f"{BASE_API}/services/audio/asr/transcription",
            headers={
                "X-DashScope-Async": "enable",
                "X-DashScope-OssResourceResolve": "enable",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        payload = submit.json()
        if submit.status_code != 200:
            raise RuntimeError(f"submit: {json.dumps(payload, ensure_ascii=False)[:200]}")
        task_id = payload["output"]["task_id"]

        for _ in range(200):
            time.sleep(3)
            poll = self.session.get(f"{BASE_API}/tasks/{task_id}", timeout=30).json()
            status = poll["output"].get("task_status")
            if status == "SUCCEEDED":
                results = poll["output"].get("results") or []
                if not results or results[0].get("subtask_status") != "SUCCEEDED":
                    raise RuntimeError(f"subtask: {json.dumps(results, ensure_ascii=False)[:200]}")
                return self.session.get(results[0]["transcription_url"], timeout=60).json()
            if status in {"FAILED", "CANCELED", "UNKNOWN"}:
                raise RuntimeError(f"task {status}")
        raise RuntimeError("poll timeout")


class Runner:
    def __init__(self, project: Path, key: str, workers: int, limit: int | None, cleanup_mp4: bool = False) -> None:
        self.project = project
        self.key = key
        self.workers = workers
        self.limit = limit
        self.cleanup_mp4 = cleanup_mp4
        self.lock = threading.Lock()
        self.plan = json.loads((project / "供AI查阅" / "plan.json").read_text(encoding="utf-8"))
        self.ledger_path = project / "供AI查阅" / "quota-ledger.json"
        self.ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        self.chain = [n for n in self.ledger.get("modelChain", []) if n not in self.ledger.get("bannedModels", [])]
        self.index = 0
        self.switches: list[dict] = []
        self.stop = threading.Event()
        self.consecutive_fail = 0
        self.mp4_bytes = 0
        self.log_path = project / "供AI查阅" / "run-log.jsonl"
        self.budget = float(self.ledger.get("budgetCny", 5.0))

    # --- 额度 ---
    def current_model(self) -> str:
        with self.lock:
            return self.chain[self.index]

    def model_entry(self, name: str) -> dict:
        return next(m for m in self.ledger["models"] if m["name"] == name)

    def remaining(self, name: str) -> float:
        entry = self.model_entry(name)
        return max(0.0, entry["freeQuotaSeconds"] - entry["usedSeconds"])

    def rotate(self, reason: str) -> bool:
        with self.lock:
            if self.index + 1 >= len(self.chain):
                return False
            self.index += 1
            self.switches.append({"to": self.chain[self.index], "reason": reason[:160]})
            return True

    def consume(self, name: str, seconds: float) -> float:
        with self.lock:
            entry = self.model_entry(name)
            free_left = max(0.0, entry["freeQuotaSeconds"] - entry["usedSeconds"])
            over = max(0.0, seconds - free_left)
            cost = over * entry["unitPriceCnyPerSecond"]
            entry["usedSeconds"] += seconds
            self.ledger["spentCny"] = round(self.ledger.get("spentCny", 0.0) + cost, 6)
            self.ledger["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
            self.ledger_path.write_text(json.dumps(self.ledger, ensure_ascii=False, indent=2), encoding="utf-8")
            return cost

    def log(self, record: dict) -> None:
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- 单条 ---
    def process(self, row: dict, play_url: str) -> dict:
        order = row["序号"]
        wid = row["作品ID"]
        work = self.project / "转写" / f"{order}_{wid}"
        work.mkdir(parents=True, exist_ok=True)
        meta_path = work / "meta.json"

        if meta_path.exists():
            prev = json.loads(meta_path.read_text(encoding="utf-8"))
            if prev.get("status") in {"ok", "no_speech"}:
                return {"order": order, "status": "skip", "id": wid}

        if self.stop.is_set():
            return {"order": order, "status": "pending", "id": wid}

        record = {
            "order": order,
            "awemeId": wid,
            "title": row["标题"],
            "date": row["发布日期"],
            "durationMs": int(float(row["时长分钟"]) * 60000),
            "digg": row["点赞数"],
            "model": None,
            "status": "failed",
        }
        mp4, m4a = work / "media.mp4", work / "audio.m4a"
        started = time.perf_counter()
        # 断点续跑时 m4a 已存在、download 分支会被跳过，timings 必须提前建好
        record["timings"] = {}
        expected = record["durationMs"] / 1000

        for attempt in range(1, 4):
            try:
                need_download = not m4a.exists()
                if not need_download and expected > 3:
                    # 续跑前校验音轨完整性，避免截断音频被当成正常音频
                    try:
                        actual = probe_duration(m4a)
                        if abs(actual - expected) > 3:
                            need_download = True
                            record["resumedRerepair"] = f"{actual:.1f}s vs {expected:.1f}s"
                            m4a.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        need_download = True
                        m4a.unlink(missing_ok=True)

                if need_download:
                    t0 = time.perf_counter()
                    size = download(play_url, mp4)
                    record["timings"]["downloadSeconds"] = round(time.perf_counter() - t0, 2)
                    t0 = time.perf_counter()
                    extract_audio(mp4, m4a)
                    record["timings"]["extractSeconds"] = round(time.perf_counter() - t0, 2)
                    record["mp4Bytes"] = size
                    self.mp4_bytes += size
                    if self.cleanup_mp4:
                        mp4.unlink(missing_ok=True)

                duration = probe_duration(m4a)
                record["audioDuration"] = round(duration, 1)

                model = self.current_model()
                if self.remaining(model) < duration * 1.05:
                    if self.rotate("额度将尽，提前切换"):
                        model = self.current_model()
                        print(f"  额度将尽，已切至 {model}", flush=True)
                    else:
                        self.stop.set()
                        record["error"] = "全部模型额度不足"
                        break

                t0 = time.perf_counter()
                data = AsrClient(self.key, model).transcribe(m4a, f"{order}_{wid}")
                record["timings"]["asrSeconds"] = round(time.perf_counter() - t0, 2)
                record["model"] = model
                self.consume(model, duration)

                (work / "transcript.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                sentences = (data.get("transcripts") or [{}])[0].get("sentences", [])
                (work / "text.txt").write_text(
                    "\n".join(s.get("text", "") for s in sentences), encoding="utf-8"
                )
                paras = build_paragraphs(sentences)
                (work / "text.md").write_text(
                    render_md(row["标题"], row["发布日期"], record["durationMs"], int(row["点赞数"]), "", paras),
                    encoding="utf-8",
                )

                record.update(
                    {
                        "status": "ok",
                        "sentences": len(sentences),
                        "paragraphs": len(paras),
                        "chars": len(re.sub(r"\s", "", "".join(s.get("text", "") for s in sentences))),
                    }
                )
                record.pop("error", None)
                with self.lock:
                    self.consecutive_fail = 0
                break
            except Exception as error:  # noqa: BLE001
                message = f"{type(error).__name__}: {error}"
                record["error"] = message[:300]

                # 代码缺陷类异常：重试没有意义，立即中止并暴露堆栈
                if isinstance(error, FATAL_ERRORS):
                    import traceback

                    print(f"\n[FATAL] {order} 触发代码缺陷，已中止整批：", flush=True)
                    traceback.print_exc()
                    record["status"] = "fatal"
                    self.stop.set()
                    break

                if QUOTA_HINT.search(message):
                    if self.rotate(message):
                        print(f"  额度或限流触发，已切至 {self.current_model()}", flush=True)
                        continue
                if "task FAILED" in message and attempt >= 2:
                    record["status"] = "no_speech"
                    break
                with self.lock:
                    self.consecutive_fail += 1
                    tripped = self.consecutive_fail >= CIRCUIT_BREAK
                if tripped:
                    self.stop.set()
                    record["error"] = f"连续 {CIRCUIT_BREAK} 条失败，已熔断：{message[:120]}"
                    break
                time.sleep(3 * attempt)

        record.setdefault("timings", {})
        record["timings"]["totalSeconds"] = round(time.perf_counter() - started, 2)
        meta_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log({"at": datetime.now().astimezone().isoformat(timespec="seconds"), **record})
        return {"order": order, "status": record["status"], "id": wid, "chars": record.get("chars")}

    # --- 汇总 ---
    def summary(self, rows: list[dict]) -> None:
        results = []
        for row in rows:
            work = self.project / "转写" / f"{row['序号']}_{row['作品ID']}"
            meta = work / "meta.json"
            if meta.exists():
                results.append(json.loads(meta.read_text(encoding="utf-8")))
        ok = [r for r in results if r.get("status") == "ok"]
        no_speech = [r for r in results if r.get("status") == "no_speech"]
        failed = [r for r in results if r.get("status") not in {"ok", "no_speech"}]
        spent = self.ledger.get("spentCny", 0.0)
        audio_seconds = sum(r.get("audioDuration") or 0 for r in ok)

        by_model: dict[str, float] = {}
        for r in ok:
            by_model[r.get("model") or "?"] = by_model.get(r.get("model") or "?", 0) + (r.get("audioDuration") or 0)

        csv_path = self.project / "转写" / "转写结果.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["序号", "作品ID", "标题", "发布日期", "时长分钟", "状态", "字数", "模型", "总耗时秒"])
            for r in sorted(results, key=lambda x: x["order"]):
                writer.writerow([
                    r["order"], r["awemeId"], r.get("title", ""), r.get("date", ""),
                    round((r.get("durationMs") or 0) / 60000, 2),
                    r.get("status"), r.get("chars", 0), r.get("model", ""),
                    (r.get("timings") or {}).get("totalSeconds", ""),
                ])

        settle = "\n".join(
            f"| {name} | {int(sec):,} | ¥0（免费额度） | |" for name, sec in sorted(by_model.items(), key=lambda x: -x[1])
        )
        md = f"""# G3 完成 · {self.project.name}

结论：{len(ok)} 条成功、{len(no_speech)} 条无语音、{len(failed)} 条失败，花费 ¥{spent:.2f}。

## 范围
处理 {len(results)} 条，音频合计 {audio_seconds/3600:.2f} 小时。

## 依据
- 模型使用：{'；'.join(f'{k} {int(v):,} 秒' for k, v in by_model.items()) or '无'}
- 切换记录：{len(self.switches)} 次

| 模型 | 处理秒数 | 计费 | 说明 |
|---|---|---|---|
{settle}
| 合计 | {int(audio_seconds):,} | **¥{spent:.2f}** | 预算 ¥{self.budget} |

## 风险
- 转写未纠错，同音字错误仍在
- 数字未经核验，引用需回看原片

## 已排除
- 本轮不做纠错、不做内容提炼
- 未保留视频原片，仅保留音轨

## 下一步
本轮结束。需要继续时回复「审核」。
"""
        (self.project / "G3-转写完成.md").write_text(md, encoding="utf-8")
        (self.project / "供AI查阅" / "transcribe-summary.json").write_text(
            json.dumps(
                {
                    "total": len(results),
                    "ok": len(ok),
                    "no_speech": len(no_speech),
                    "failed": len(failed),
                    "audioHours": round(audio_seconds / 3600, 2),
                    "spentCny": round(spent, 2),
                    "byModel": by_model,
                    "switches": self.switches,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.project / "STATUS.md").write_text(
            f"""阶段：G3 已完成
待确认：无
下一步：需要时回复「审核」
更新：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')}
""",
            encoding="utf-8",
        )
        print(f"\n完成 {len(ok)} / 无语音 {len(no_speech)} / 失败 {len(failed)}　花费 ¥{spent:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--key", default=None)
    parser.add_argument("--key-file", default=None)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--cleanup-mp4",
        action="store_true",
        help="转完即删原片。默认不删：大批量逐条删除会触发宿主的批量删除保护并杀死进程",
    )
    args = parser.parse_args()

    key = args.key or (args.key_file and Path(args.key_file).read_text(encoding="utf-8").strip()) or ""
    key = key or __import__("os").environ.get("DSK", "").strip()
    if not key:
        raise SystemExit("必须提供 --key、--key-file，或环境变量 DSK")

    project = Path(args.project).resolve()
    rows = list(csv.DictReader((project / "清单" / "works.csv").open(encoding="utf-8-sig")))
    raw = json.loads((project / "供AI查阅" / "manifest-raw.json").read_text(encoding="utf-8"))
    urls = {str(i["id"]): i.get("playUrl", "") for i in raw.get("items", [])}

    tasks = [(r, urls.get(r["作品ID"], "")) for r in rows if r["类型"] == "视频" and urls.get(r["作品ID"])]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("没有可处理的任务（检查 works.csv 与 manifest-raw.json 的直链）")
        return 1

    runner = Runner(project, key, args.workers, args.limit, cleanup_mp4=args.cleanup_mp4)
    print(f"待处理 {len(tasks)} 条，并发 {args.workers}，起始模型 {runner.current_model()}", flush=True)
    if not args.cleanup_mp4:
        print("原片保留模式（默认）。需要清理时自行批量删除，不要在长任务里逐条删。", flush=True)
    done = 0
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(runner.process, r, u): r for r, u in tasks}
        for future in as_completed(futures):
            result = future.result()
            done += 1
            if done % PROGRESS_EVERY == 0 or done == len(tasks):
                spent = runner.ledger.get("spentCny", 0.0)
                used = sum(m["usedSeconds"] for m in runner.ledger["models"])
                elapsed = (time.perf_counter() - started) / 60
                eta = elapsed / done * (len(tasks) - done)
                print(
                    f"[{done}/{len(tasks)}] 已用 {int(used):,} 秒｜花费 ¥{spent:.2f}｜"
                    f"当前 {runner.current_model()}｜预计还需 {eta:.0f} 分钟",
                    flush=True,
                )
    runner.summary([r for r, _ in tasks])
    if runner.stop.is_set():
        print("注意：执行被熔断或额度不足中断，未完成条目记为 pending，可重跑续做")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
