#!/usr/bin/env python3
"""Read the account's per-note performance off the 小红书创作服务平台.

Why this exists: `rednote-assistant`'s `xhs_state.py` scrapes the note-manager
DOM and only gets 阅读/赞/藏/转 — and only for the notes that happen to be on
that page. The 数据看板 (`/statistics/data-analysis`) carries more, and it is
peer-normalised:

    曝光 · 观看 · 封面点击率 · 点赞 · 评论 · 收藏 · 涨粉 · 分享 · 人均观看时长
    每篇的同类诊断（内容丰富度 / 封面点击率 / 内容深度 / 互动表现 分位）
    观看来源（首页推荐 / 搜索 / 视频推荐 …）· 观众画像

Without these, 选题与封面 只能凭感觉——`rsi-reading` 的「回流 A」那条边。
`fetch` 落一份快照到 `<workspace>/state/analytics/`，`report` 读它出表。

The API is signed (`X-s` / `X-t` are computed per URL by the page), so we cannot
replay or edit requests. We let the page make them and capture the responses —
see `HOOK_JS`. Consequence: this drives the user's own Chrome tab.

Subcommands:
  fetch  [--out D] [--max-age-h N]   抓一份快照（默认按天覆盖），已有新鲜快照就跳过
  report [--file F]                  读快照出表（默认取最新一份，不碰网络）

Requires the same bridge as `xhs.py` (see references/publishing.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, date
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("XHS_SKILLS_DIR", "/tmp/xhs-skills-repo"))
BRIDGE_URL = os.environ.get("XHS_BRIDGE_URL", "ws://localhost:9333")
WORKSPACE = Path(os.environ.get("XHS_WORKSPACE", Path.home() / "Documents/rednote"))
DATA_URL = "https://creator.xiaohongshu.com/statistics/data-analysis"
DETAIL_URL = "https://creator.xiaohongshu.com/statistics/note-detail?noteId="
LIST_RE = "/note/analyze/list"
BASE_RE = "/note/base?note_id="
SOURCE_RE = "/audience/source?note_id="

QUOTAS = {
    "contentRich": "内容丰富度",
    "clickRate": "封面点击率",
    "followFromDiscovery": "涨粉",
    "noteEngage": "互动表现",
    "viewTimeAvg": "内容深度",
    "interactRate": "互动率",
}

# 页面自己的 XHR 带签名（X-s/X-t 与 URL 绑定），所以只能让页面发请求、我们收响应。
# 挂在 MAIN world 的 window 上——SPA 内部跳转不会清掉它，整轮抓取共用一份。
HOOK_JS = r"""(() => {
  if (window.__XHSA) return 'already';
  window.__XHSA = {reqs: [], resp: {}};
  const keep = (u) => /note\/analyze\/list|note\/base\?note_id=|note\/audience\/source\?note_id=/.test(u);
  const oOpen = XMLHttpRequest.prototype.open;
  const oSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u, ...r) { this.__u = u; return oOpen.call(this, m, u, ...r); };
  XMLHttpRequest.prototype.send = function (b) {
    const u = this.__u || '';
    if (keep(u)) {
      this.addEventListener('load', () => {
        window.__XHSA.resp[u] = {status: this.status, text: this.responseText || ''};
      });
    }
    return oSend.call(this, b);
  };
  return 'installed';
})()"""


def reexec_in_venv() -> None:
    """桥接依赖 websockets，它装在 skills 仓库的 venv 里，这里换解释器再跑。"""
    if os.environ.get("_XHS_REEXEC"):
        return
    try:
        import websockets  # noqa: F401
        return
    except ImportError:
        pass
    for candidate in (SKILLS_DIR / ".venv" / "bin" / "python",
                      SKILLS_DIR / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            os.environ["_XHS_REEXEC"] = "1"
            os.execv(str(candidate), [str(candidate), os.path.abspath(__file__), *sys.argv[1:]])


reexec_in_venv()


def die(msg: str, code: int = 2):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


def bridge():
    sys.path.insert(0, str(SKILLS_DIR / "scripts"))
    try:
        from xhs.bridge import BridgePage
        from xhs.errors import CDPError
    except ImportError as e:
        die(f"无法从 {SKILLS_DIR} 导入 xhs 模块：{e}\n"
            "先按 references/publishing.md 跑 scripts/setup_bridge.sh，"
            "或用 XHS_SKILLS_DIR 指向仓库。")
    page = BridgePage(BRIDGE_URL)
    if not page.is_server_running():
        die("bridge server 没在跑。先在 rednote-post 里跑 `python3 scripts/xhs.py status`。")
    if not page.is_extension_connected():
        die("浏览器扩展没连上。打开装了 XHS Bridge 扩展的 Chrome 再试。")
    return page, CDPError


def latest_snapshot() -> Path | None:
    files = sorted((WORKSPACE / "state" / "analytics").glob("analytics-*.json"))
    return files[-1] if files else None


# --------------------------------------------------------------------- fetch

def spush(page, route: str) -> None:
    """SPA 跳转。用 router 而不是 navigate——换页不会重载，钩子才留得住。"""
    page.evaluate("""(() => {
      const app = document.querySelector('#app');
      const r = app && app.__vue_app__ && app.__vue_app__.config.globalProperties.$router;
      if (!r) return 'no-router';
      r.push(%s);
      return 'ok';
    })()""" % json.dumps(route))


def captured(page) -> dict:
    raw = page.evaluate("JSON.stringify(window.__XHSA ? window.__XHSA.resp : {})")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def wait_for(page, pred, timeout: float = 20.0, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = captured(page)
        hit = pred(got)
        if hit:
            return hit
        time.sleep(interval)
    return None


def best_list(resps: dict) -> list[dict] | None:
    """页面上有多份 list 响应（翻页/改页长都会各来一份），取条数最多的那份。"""
    best = None
    for url, r in resps.items():
        if LIST_RE not in url:
            continue
        try:
            infos = json.loads(r["text"])["data"]["note_infos"]
        except (KeyError, ValueError, TypeError):
            continue
        if best is None or len(infos) > len(best):
            best = infos
    return best


def fetch_list(page) -> list[dict]:
    # 从别的路由跳回来才会重新拉数据；已在 data-analysis 上原地 push 不触发。
    spush(page, "/statistics/account")
    time.sleep(1.5)
    spush(page, "/statistics/data-analysis")
    notes = wait_for(page, lambda r: best_list(r) or None, timeout=25)
    if not notes:
        die("没抓到笔记列表。确认 Chrome 停在创作服务平台且已登录。")

    # 默认一页 10 条；不够就改页长再等一份新的 list 响应。
    if len(notes) < 20:
        page._call("click_nth_element", {"selector": ".d-select", "index": 1})
        time.sleep(1.5)
        try:
            page._call("click_element_by_text",
                       {"selector": ".d-option-content", "text": "50 条/页"})
        except Exception:
            pass
        bigger = wait_for(page, lambda r: (best_list(r) if best_list(r) and
                                           len(best_list(r)) > len(notes) else None),
                          timeout=20)
        if bigger:
            notes = bigger
    return notes


def fetch_details(page, notes: list[dict]) -> dict:
    out = {"base": {}, "source": {}}
    for i, n in enumerate(notes, 1):
        nid = n["id"]
        spush(page, DETAIL_URL + nid)
        got = wait_for(page, lambda r, nid=nid: r.get(
            f"https://creator.xiaohongshu.com/api/galaxy/creator/datacenter/note/base?note_id={nid}")
            or None, timeout=20)
        if got:
            try:
                out["base"][nid] = json.loads(got["text"])["data"]
            except (KeyError, ValueError, TypeError):
                pass
        resps = captured(page)
        for url, r in resps.items():
            if SOURCE_RE in url and url.split("note_id=")[-1] == nid:
                try:
                    out["source"][nid] = json.loads(r["text"])["data"]
                except (KeyError, ValueError, TypeError):
                    pass
        print(f"  [{i}/{len(notes)}] {n['title'][:24]}", file=sys.stderr)
    return out


def cmd_fetch(args) -> int:
    out_dir = Path(args.out) if args.out else WORKSPACE / "state" / "analytics"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    target = out_dir / f"analytics-{today}.json"

    if target.exists() and args.max_age_h > 0:
        age_h = (time.time() - target.stat().st_mtime) / 3600
        if age_h < args.max_age_h:
            print(f"已有 {age_h:.1f} 小时内的快照，跳过：{target}")
            return 0

    page, _ = bridge()
    if "creator.xiaohongshu.com" not in (page.evaluate("location.href") or ""):
        page.navigate(DATA_URL)
        time.sleep(6)
    print("安装抓取钩子…", file=sys.stderr)
    page.evaluate(HOOK_JS)

    print("拉笔记列表…", file=sys.stderr)
    notes = fetch_list(page)
    print(f"  {len(notes)} 篇", file=sys.stderr)
    print("逐篇拉分析详情…", file=sys.stderr)
    detail = fetch_details(page, notes)

    snap = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "source_url": DATA_URL,
        "notes": notes,
        **detail,
    }
    target.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    print(f"已写入 {target}")
    return 0


# -------------------------------------------------------------------- report

def load_snapshot(path: str | None) -> dict:
    p = Path(path) if path else latest_snapshot()
    if not p or not p.exists():
        die("还没有快照。先跑 `python3 scripts/analytics.py fetch`。")
    print(f"# 快照：{p}  （{json.loads(p.read_text())['fetched_at']}）\n")
    return json.loads(p.read_text())


def cmd_report(args) -> int:
    snap = load_snapshot(args.file)
    notes = snap["notes"]
    base = snap.get("base", {})
    source = snap.get("source", {})

    rows = []
    for n in notes:
        read = n.get("read_count") or 0
        fav = n.get("fav_count") or 0
        b = base.get(n["id"], {})
        ai = {x["quota"]: x for x in b.get("analyse_infos", [])}
        srcs = source.get(n["id"], {})
        srcs = srcs.get("data", srcs)
        rec = next((s for s in srcs.get("source", [])
                    if s.get("title") == "首页推荐"), None)
        rows.append({
            "t": n["title"], "when": datetime.fromtimestamp(n["post_time"] / 1000),
            "imp": n.get("imp_count"), "read": read, "ctr": n.get("coverClickRate") or 0,
            "like": n.get("like_count") or 0, "fav": fav,
            "fav_rate": (fav / read * 100) if read else 0.0,
            "watch": n.get("view_time_avg") or 0,
            "feed": (rec or {}).get("value"),
            "srcs": {x.get("title"): x.get("value") for x in srcs.get("source", [])},
            "diag": ai,
        })
    rows.sort(key=lambda r: r["when"])

    print(f"{'发布':16s} {'标题':22s} {'曝光':>5} {'观看':>4} {'点击率':>6} {'收藏':>4} "
          f"{'收藏率':>6} {'时长':>5} {'首页':>4}")
    for r in rows:
        feed = f"{r['feed']}%" if r["feed"] is not None else "  -"
        print(f"{r['when']:%m-%d %H:%M}     {r['t'][:20]:22s} {r['imp']:>5} {r['read']:>4} "
              f"{r['ctr']*100:5.1f}% {r['fav']:>4} {r['fav_rate']:5.1f}% {r['watch']:>4}s {feed:>5}")

    diag = [(r, r["diag"]) for r in rows if r["diag"]]
    if diag:
        print("\n## 同类诊断（分位 = 超过多少同类作者；封面点击率中位数之上才算及格）")
        for r, ai in diag:
            if "clickRate" not in ai:
                continue
            q = ai["clickRate"]
            parts = [f"封面 {q['count']*100:.1f}% vs 同类中位数 {q['median']*100:.1f}% "
                     f"→ {q['summary']}"]
            for k in ("contentRich", "viewTimeAvg", "interactRate"):
                if k in ai:
                    parts.append(f"{QUOTAS[k]} {ai[k]['summary']}")
            print(f"- {r['t'][:20]:22s} " + " ｜ ".join(parts))
    else:
        print("\n（这批笔记还没有同类诊断——平台要积累一天左右才出。）")

    with_src = [r for r in rows if r["srcs"]]
    if with_src:
        print("\n## 观看来源（首次分发靠哪条口子；搜索占比高 = 标题/标签被搜中）")
        for r in with_src:
            assert r["srcs"]
            top = sorted(r["srcs"].items(), key=lambda kv: -(kv[1] or 0))[:4]
            print(f"- {r['t'][:20]:22s} " + "  ".join(f"{k} {v}%" for k, v in top))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="读小红书创作服务平台的数据看板")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="抓一份快照到 state/analytics/")
    p.add_argument("--out", help="输出目录（默认 <workspace>/state/analytics）")
    p.add_argument("--max-age-h", type=float, default=6.0,
                   help="当天已有这么新的快照就跳过（默认 6 小时；0 = 总是重抓）")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("report", help="读最新快照出表")
    p.add_argument("--file", help="指定快照文件")
    p.set_defaults(fn=cmd_report)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
