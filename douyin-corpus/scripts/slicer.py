"""长音频切片转写：静音点吸附切片 + 分片缓存 + 时间轴偏移合并。

用法：被 transcribe.py 调用，不独立运行。

规则（来自实战）：
- 单条 > 25 分钟自动走切片
- 切点吸附静音中点，目标每 15 分钟一片
- 切点不得越过片尾（留 15 秒余量）
- 极短片（< 3 秒）不提交 ASR
- 每片缓存到 _slices/pXX.json，重跑只补失败片
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SLICE_THRESHOLD_SEC = 25 * 60  # 超过 25 分钟走切片
TARGET_SLICE_SEC = 15 * 60  # 目标每片 15 分钟
SILENCE_MARGIN = 120  # 切点吸附范围 ±120 秒
MIN_SLICE_SEC = 3  # 极短片不提交
EDGE_GUARD = 15  # 切点距片尾至少 15 秒


def detect_silences(audio: Path) -> list[float]:
    """用 ffmpeg silencedetect 找静音中点（秒）。"""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(audio),
                "-af", "silencedetect=noise=-32dB:d=0.4",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    silences = []
    start = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].strip().split()[0])
            except (ValueError, IndexError):
                start = None
        elif "silence_end:" in line and start is not None:
            try:
                end = float(line.split("silence_end:")[1].strip().split()[0])
                silences.append((start + end) / 2)
            except (ValueError, IndexError):
                pass
            start = None
    return silences


def plan_slices(duration_sec: float, silences: list[float]) -> list[float]:
    """生成切点列表（秒），吸附到最近的静音中点。"""
    if duration_sec <= SLICE_THRESHOLD_SEC:
        return []

    cuts = [0.0]
    target = TARGET_SLICE_SEC
    while target < duration_sec - EDGE_GUARD:
        # 在 target ± SILENCE_MARGIN 内找最近的静音
        best = None
        best_dist = SILENCE_MARGIN + 1
        for s in silences:
            if target - SILENCE_MARGIN <= s <= target + SILENCE_MARGIN:
                dist = abs(s - target)
                if dist < best_dist:
                    best = s
                    best_dist = dist
        cut = best if best is not None else target
        # 切点不得越过片尾
        if cut >= duration_sec - EDGE_GUARD:
            break
        # 切点不得与上一个切点太近
        if cut - cuts[-1] < MIN_SLICE_SEC:
            cut = cuts[-1] + TARGET_SLICE_SEC
            if cut >= duration_sec - EDGE_GUARD:
                break
        cuts.append(cut)
        target = cut + TARGET_SLICE_SEC

    cuts.append(duration_sec)
    return cuts


def cut_slice(audio: Path, output: Path, start: float, end: float) -> None:
    """切出一段音频。"""
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(audio),
            "-ss", str(start), "-to", str(end),
            "-c", "copy", str(output),
        ],
        check=True,
    )


def slice_and_transcribe(
    audio: Path,
    work_dir: Path,
    transcribe_fn,
    model: str,
) -> dict | None:
    """切片 + 逐片转写 + 合并。

    transcribe_fn(m4a_path, tag) -> dict  # 返回 transcript.json 格式
    返回合并后的 transcript dict，或 None（全部失败）。
    """
    import subprocess as _sp

    # 获取音频时长
    probe = _sp.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        capture_output=True, text=True, check=True,
    )
    duration = float(probe.stdout.strip())

    silences = detect_silences(audio)
    cuts = plan_slices(duration, silences)
    if not cuts:
        return None  # 不需要切片

    slice_dir = work_dir / "_slices"
    slice_dir.mkdir(exist_ok=True)

    all_sentences = []
    all_words = []
    failed_slices = []

    for i in range(len(cuts) - 1):
        start, end = cuts[i], cuts[i + 1]
        slice_dur = end - start
        if slice_dur < MIN_SLICE_SEC:
            continue

        cache_file = slice_dir / f"p{i:02d}.json"

        # 检查缓存
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("_sliceDur") == round(slice_dur, 1):
                    # 缓存有效，直接用
                    _merge_sentences(all_sentences, all_words, cached, start)
                    continue
            except (json.JSONDecodeError, KeyError):
                pass

        # 切片 + 转写
        slice_audio = slice_dir / f"p{i:02d}.m4a"
        cut_slice(audio, slice_audio, start, end)

        try:
            result = transcribe_fn(slice_audio, f"slice_{i:02d}")
        except Exception:
            failed_slices.append(i)
            slice_audio.unlink(missing_ok=True)
            continue

        if result is None:
            failed_slices.append(i)
            slice_audio.unlink(missing_ok=True)
            continue

        # 缓存结果
        result["_sliceDur"] = round(slice_dur, 1)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        _merge_sentences(all_sentences, all_words, result, start)
        slice_audio.unlink(missing_ok=True)

    if not all_sentences:
        return None

    return {
        "transcripts": [
            {
                "sentences": all_sentences,
                "words": all_words,
            }
        ],
        "_sliced": True,
        "_totalSlices": len(cuts) - 1,
        "_failedSlices": failed_slices,
    }


def _merge_sentences(
    all_sentences: list, all_words: list, slice_result: dict, offset_sec: float
) -> None:
    """把一片的结果按时间偏移合并进总列表。"""
    offset_ms = int(offset_sec * 1000)
    for t in slice_result.get("transcripts", [{}]):
        for s in t.get("sentences", []):
            all_sentences.append(
                {
                    "begin_time": s["begin_time"] + offset_ms,
                    "end_time": s["end_time"] + offset_ms,
                    "text": s.get("text", ""),
                    "words": [
                        {
                            "begin_time": w["begin_time"] + offset_ms,
                            "end_time": w["end_time"] + offset_ms,
                            "text": w.get("text", ""),
                            "punctuation": w.get("punctuation", ""),
                        }
                        for w in s.get("words", [])
                    ],
                }
            )
        for w in t.get("words", []):
            all_words.append(
                {
                    "begin_time": w["begin_time"] + offset_ms,
                    "end_time": w["end_time"] + offset_ms,
                    "text": w.get("text", ""),
                    "punctuation": w.get("punctuation", ""),
                }
            )
