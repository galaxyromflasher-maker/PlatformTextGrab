"""抖音浏览器层：登录、抖音号解析、主页全量抓取。

依赖：pip install playwright（复用本机 Edge，不下载 Chromium）

用法：
    python browser.py check
    python browser.py login --profile <目录>
    python browser.py resolve --id 1318587176
    python browser.py capture --url <主页URL> --out raw.json --profile <目录>

约定：
    - 登录成功会**主动关闭窗口**，这是正常行为，不是崩溃
    - 二维码不截图（异步渲染，截到的是占位图），直接开有窗口的浏览器让用户扫
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CAPTURE_JS = SKILL_DIR / "assets" / "capture.js"
DEFAULT_PROFILE = Path.cwd() / "_browser-profile"


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        return sync_playwright
    except ImportError:
        raise SystemExit(
            "缺少 playwright。先执行：pip install playwright\n"
            "不要执行 playwright install chromium —— 直接用本机 Edge 即可。"
        )


def _new_context(playwright, profile: Path, headless: bool):
    profile.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        channel="msedge",
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1440, "height": 900},
    )


def cmd_check() -> int:
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
            version = browser.version
            browser.close()
            print(json.dumps({"ok": True, "edge": version}, ensure_ascii=False))
            return 0
        except Exception as error:  # noqa: BLE001
            print(json.dumps({"ok": False, "error": str(error)[:300]}, ensure_ascii=False))
            return 1


def cmd_login(profile: Path) -> int:
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        context = _new_context(p, profile, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60000)
        print("浏览器已打开。请在窗口里扫码登录，登录成功后窗口会自动关闭。", flush=True)

        deadline = time.time() + 300
        while time.time() < deadline:
            cookies = context.cookies()
            if any(c.get("name") in {"sessionid", "sessionid_ss"} and c.get("value") for c in cookies):
                print(json.dumps({"ok": True, "loggedIn": True}, ensure_ascii=False))
                context.close()
                return 0
            time.sleep(2)
        context.close()
        print(json.dumps({"ok": False, "error": "300 秒内未检测到登录态"}, ensure_ascii=False))
        return 1


def cmd_resolve(account_id: str, profile: Path) -> int:
    """抖音号 -> 主页 sec_uid"""
    sync_playwright = _import_playwright()
    with sync_playwright() as p:
        context = _new_context(p, profile, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            f"https://www.douyin.com/search/{account_id}?type=user",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        # 等搜索结果渲染出来（异步加载，sleep 不可靠）
        try:
            page.wait_for_selector('a[href*="/user/"]', state="attached", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        hrefs = page.eval_on_selector_all('a[href*="/user/"]', "els => els.map(e => e.getAttribute('href'))")
        context.close()

    seen, out = set(), []
    for href in hrefs or []:
        clean = (href or "").split("?")[0]
        if "/user/" in clean and clean not in seen:
            seen.add(clean)
            out.append("https://www.douyin.com" + clean)
    print(json.dumps({"ok": bool(out), "candidates": out[:10]}, ensure_ascii=False, indent=2))
    return 0 if out else 1


def cmd_capture(url: str, out_path: Path, profile: Path, max_scroll: int = 300) -> int:
    sync_playwright = _import_playwright()
    script = CAPTURE_JS.read_text(encoding="utf-8")

    with sync_playwright() as p:
        context = _new_context(p, profile, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.add_init_script(script)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)

        # 滚动容器不是 window，是 div.parent-route-container
        result = page.evaluate(
            """async (maxScroll) => {
                const sleep = (ms) => new Promise(r => setTimeout(r, ms));
                const sc = document.querySelector('div.parent-route-container');
                if (!sc) return { error: 'scroll-container-not-found' };
                const count = () => document.querySelectorAll('ul[data-e2e=scroll-list] li a').length;
                let last = 0, stable = 0;
                for (let i = 0; i < maxScroll; i++) {
                    sc.scrollTo(0, sc.scrollHeight);
                    await sleep(1200);
                    const c = count();
                    if (c === last) stable++; else { stable = 0; last = c; }
                    if (stable >= 4) break;
                }
                const wall = document.body.innerText.indexOf('登录后查看更多') >= 0;
                return { links: last, loginWall: wall };
            }""",
            max_scroll,
        )
        data = page.evaluate("() => window.__dumpDouyinCorpus && window.__dumpDouyinCorpus()")
        context.close()

    payload = json.loads(data) if isinstance(data, str) else data
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    summary = {
        "captured": payload.get("count"),
        "tabTotal": payload.get("tabTotal"),
        "scroll": result,
        "out": str(out_path),
    }
    if result.get("loginWall"):
        summary["error"] = "出现登录墙，清单不完整（约 21 条）。请先执行 login 扫码登录，再重新 capture。"
        summary["ok"] = False
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["check", "login", "resolve", "capture"])
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE))
    parser.add_argument("--id", default=None, help="抖音号（resolve 用）")
    parser.add_argument("--url", default=None, help="主页 URL（capture 用）")
    parser.add_argument("--out", default="capture-raw.json")
    args = parser.parse_args()

    profile = Path(args.profile).expanduser()

    if args.action == "check":
        return cmd_check()
    if args.action == "login":
        return cmd_login(profile)
    if args.action == "resolve":
        if not args.id:
            raise SystemExit("resolve 需要 --id")
        return cmd_resolve(args.id, profile)
    if not args.url:
        raise SystemExit("capture 需要 --url")
    return cmd_capture(args.url, Path(args.out), profile)


if __name__ == "__main__":
    sys.exit(main())
