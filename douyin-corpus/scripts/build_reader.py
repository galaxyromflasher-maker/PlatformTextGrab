"""生成单文件 HTML 浏览器，方便快速检查转写质量。

用法：
    python build_reader.py --project <博主目录>
    python build_reader.py --project <博主目录> --out 浏览.html
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

PARA = re.compile(r"^\[(\d{2}:\d{2}(?:[–-]\d{2}:\d{2})?)\]\s*(.+)$")

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#fafafa;--fg:#1a1a1a;--sub:#666;--line:#e0e0e0;--mark:#fff3a0;--accent:#1a73e8}
*{margin:0;padding:0;box-sizing:border-box}
body{font:15px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--fg)}
.wrap{display:flex;height:100vh}
.side{width:320px;border-right:1px solid var(--line);overflow-y:auto;padding:16px;flex-shrink:0}
.main{flex:1;overflow-y:auto;padding:32px 40px;max-width:820px}
h1{font-size:18px;margin-bottom:12px}
.search{width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:6px;font-size:14px;margin-bottom:12px}
.count{font-size:12px;color:var(--sub);margin-bottom:8px}
.item{padding:8px 10px;border-radius:6px;cursor:pointer;font-size:13px;line-height:1.5;margin-bottom:2px}
.item:hover{background:#eee}
.item.active{background:var(--accent);color:#fff}
.item .meta{font-size:11px;color:var(--sub);margin-top:2px}
.item.active .meta{color:rgba(255,255,255,.7)}
.doc h2{font-size:20px;margin-bottom:4px}
.doc .info{font-size:13px;color:var(--sub);margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid var(--line)}
.para{margin-bottom:16px}
.para .ts{font-size:12px;color:var(--sub);font-family:monospace;margin-right:8px}
mark{background:var(--mark);padding:0 1px}
.empty{color:var(--sub);text-align:center;margin-top:80px}
@media(max-width:700px){.wrap{flex-direction:column}.side{width:100%;height:40vh;border-right:none;border-bottom:1px solid var(--line)}}
</style>
</head>
<body>
<div class="wrap">
<div class="side">
<h1>__TITLE__</h1>
<input class="search" id="q" placeholder="搜索标题或正文" autofocus>
<div class="count" id="count"></div>
<div id="list"></div>
</div>
<div class="main"><div class="empty" id="empty">从左侧选一篇</div><div class="doc" id="doc" style="display:none"></div></div>
</div>
<script>
const DATA = __DATA__;
let cur = -1;
function render(list, q) {
  const el = document.getElementById('list');
  el.innerHTML = list.map((d, i) =>
    '<div class="item'+(i===cur?' active':'')+'" onclick="show('+d._i+')">'+
    '<div>'+esc(d.title||'(无标题)')+'</div>'+
    '<div class="meta">'+esc(d.date)+' · '+d.chars+'字 · '+d.paras+'段</div></div>'
  ).join('');
  document.getElementById('count').textContent = q ? list.length+' 条匹配' : '共 '+DATA.length+' 篇';
}
function show(i) {
  cur = DATA.findIndex(d=>d._i===i);
  const d = DATA[cur];
  const q = document.getElementById('q').value.trim();
  document.getElementById('empty').style.display='none';
  const el = document.getElementById('doc');
  el.style.display='';
  let body = d.paragraphs.map(p =>
    '<div class="para"><span class="ts">'+esc(p.t)+'</span>'+hl(p.text, q)+'</div>'
  ).join('');
  el.innerHTML = '<h2>'+hl(d.title||'(无标题)', q)+'</h2><div class="info">'+
    esc(d.date)+' · '+(d.duration||0).toFixed(1)+' 分钟 · '+(d.digg||0)+' 赞</div>'+body;
  render(DATA, q);
  document.querySelector('.main').scrollTop = 0;
}
function search() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  if (!q) { cur=-1; render(DATA, ''); document.getElementById('doc').style.display='none'; document.getElementById('empty').style.display=''; return; }
  const hit = DATA.filter(d => (d.title||'').toLowerCase().includes(q) || d.paragraphs.some(p=>p.text.toLowerCase().includes(q)));
  cur = -1;
  render(hit, q);
}
function hl(s, q) {
  if (!q) return esc(s);
  const i = s.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return esc(s);
  return esc(s.slice(0,i))+'<mark>'+esc(s.slice(i,i+q.length))+'</mark>'+esc(s.slice(i+q.length));
}
function esc(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
document.getElementById('q').addEventListener('input', search);
DATA.forEach((d,i)=>d._i=i);
render(DATA, '');
</script>
</body>
</html>"""


def collect(project: Path) -> list[dict]:
    items = []
    for folder in sorted((project / "转写").iterdir()):
        if not folder.is_dir():
            continue
        md = folder / "text.md"
        meta_path = folder / "meta.json"
        if not md.exists():
            continue
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        paras = []
        for line in md.read_text(encoding="utf-8").splitlines():
            m = PARA.match(line.strip())
            if m:
                paras.append({"t": m.group(1), "text": m.group(2).strip()})
        items.append(
            {
                "title": meta.get("title", ""),
                "date": meta.get("date", ""),
                "duration": (meta.get("durationMs") or 0) / 60000,
                "digg": meta.get("digg", 0),
                "chars": meta.get("chars", sum(len(p["text"]) for p in paras)),
                "paras": len(paras),
                "paragraphs": paras,
            }
        )
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    if not (project / "转写").exists():
        raise SystemExit(f"没有 转写/ 目录：{project}")

    items = collect(project)
    if not items:
        raise SystemExit("没有找到任何 text.md")

    creator = project.name.split("-")[0]
    title = f"{creator} · 转写浏览"
    out = Path(args.out) if args.out else project / f"{creator}-转写浏览.html"

    html = TEMPLATE.replace("__DATA__", json.dumps(items, ensure_ascii=False)).replace("__TITLE__", title)
    out.write_text(html, encoding="utf-8")

    total_chars = sum(i["chars"] for i in items)
    print(f"已生成：{out}")
    print(f"  {len(items)} 篇 / {total_chars:,} 字 / {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
