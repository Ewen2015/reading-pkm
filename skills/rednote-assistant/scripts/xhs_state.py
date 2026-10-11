#!/usr/bin/env python3
"""只读地读取小红书创作者中心状态，供排期与库存体检使用。

扫描四类事实（都通过 rednote-post 用的同一个浏览器桥接）：
  queue    未来定时发布队列（note-manager 全部 tab 的 .note-card__schedule），
           并标注哪些还卡在审核中
  drafts   草稿箱（发布页 .draft-title 抽屉，按 视频/图文/长文/播客 分类）
  history  已发布笔记的发布时间 + 五项数据（note-manager 已发布 tab）
  all      以上三项

这里不点任何会改变的按钮：只切 tab、开抽屉、滚动。发布动作仍然归 rednote-post。

用法：
  python3 xhs_state.py queue   [--max 40] [--out state/queue.json]
  python3 xhs_state.py drafts  [--out state/drafts.json]
  python3 xhs_state.py history [--max 60] [--out state/history.json]
  python3 xhs_state.py all     [--out state/state.json]

输出为 JSON（stdout 或 --out）。字段见 references/plan-schema.md。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("XHS_SKILLS_DIR", "/tmp/xhs-skills-repo"))
BRIDGE_URL = os.environ.get("XHS_BRIDGE_URL", "ws://localhost:9333")
NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"
PUBLISH_URL = ("https://creator.xiaohongshu.com/publish/publish"
               "?source=official&target=image")

# 平台在卡片上的固定顺序；用错顺序会把点赞当阅读量。
STAT_ORDER = ["reads", "comments", "likes", "collects", "shares"]

# 卡片解析：noteId 藏在 data-impression 的 JSON 里（HTML 转义过）。
CARD_JS = r"""(() => {
  const decode = (s) => (s || '').replace(/&quot;/g, '"').replace(/&amp;/g, '&');
  const num = (s) => {
    const t = String(s == null ? '' : s).replace(/[,\s]/g, '');
    const m = t.match(/(-?\d+(?:\.\d+)?)\s*(万|w)?/i);
    if (!m) return null;
    return Math.round(Number(m[1]) * (m[2] ? 10000 : 1));
  };
  const txt = (el) => (el ? (el.innerText || '').trim() : null);
  return JSON.stringify([...document.querySelectorAll('.note-card')].map(c => {
    let noteId = null;
    try {
      noteId = JSON.parse(decode(c.getAttribute('data-impression')))
                 .noteTarget.value.noteId;
    } catch (e) {}
    const raw = [...c.querySelectorAll('.note-card__row--stats .note-card__stat span')]
                  .map(e => (e.innerText || '').trim());
    return {
      noteId,
      title: txt(c.querySelector('.note-card__title')),
      time: txt(c.querySelector('.note-card__time')),
      scheduled: !!c.querySelector('.note-card__schedule'),
      statsRaw: raw,
      stats: raw.map(num),
    };
  }));
})()"""

TABS_JS = r"""(() => JSON.stringify(
  [...document.querySelectorAll('.tab-item')].map(e => (e.innerText || '').trim())
))()"""

PAGE_JS = r"""(() => JSON.stringify({
  loading: /正在加载|加载中/.test(document.body.innerText),
  focused: document.hasFocus(),
  visibility: document.visibilityState,
  inner_height: window.innerHeight,
  outer_height: window.outerHeight,
}))()"""

# 虚拟列表的下一页请求**只在窗口是 key window 时才发**。而自动化跑起来时，宿主程序
# （Codex 桌面端）在命令执行期间一直占着前台，Chrome 永远拿不到 key window，
# `document.hasFocus()` 恒为 false —— 于是每次都只读到首屏 10 张、报 `partial`。
# 2026-10-09 实测：在主 world 把 `document.hasFocus` 钉成 true 之后，滚动立刻能触发
# 下一页，读全了 267 篇。这里只是让页面**以为**自己在前台，不改任何数据、
# 也不点任何会改变状态的按钮，仍然是纯读操作。
FOCUS_PATCH_JS = r"""(() => {
  try {
    Object.defineProperty(document, 'hasFocus', {value: () => true, configurable: true});
  } catch (e) {
    document.hasFocus = () => true;
  }
  return document.hasFocus();
})()"""

# 笔记列表是虚拟滚动的：首屏只挂约 10 张卡片，往下要靠滚动触发下一页。
# 真正的滚动容器不是 window（window 的 scrollHeight == clientHeight，滚不动），
# 而是 .note-card 的某个可滚动祖先（实测是 .list-container-box / .microapp-container）。
SCROLL_JS = r"""(() => {
  const scrollable = (e) => {
    if (!e || !e.scrollHeight) return false;
    const cs = getComputedStyle(e);
    return /(auto|scroll)/.test(cs.overflowY) && e.scrollHeight > e.clientHeight + 4;
  };
  const found = [];
  const push = (e) => { if (scrollable(e) && !found.includes(e)) found.push(e); };
  let n = document.querySelector('.note-card');
  while (n) { push(n); n = n.parentElement; }
  ['.list-container-box', '.microapp-container', '.content', '.notes-container']
    .forEach(sel => document.querySelectorAll(sel).forEach(push));
  window.scrollTo(0, document.body.scrollHeight);
  let moved = 0;
  found.forEach(e => {
    const before = e.scrollTop;
    e.scrollTop = e.scrollHeight;
    if (e.scrollTop !== before) moved += 1;
    e.dispatchEvent(new Event('scroll', {bubbles: true}));
  });
  return JSON.stringify({scrollers: found.length, moved});
})()"""

CLICK_TAB_JS = r"""(() => {
  const want = %s;
  const t = [...document.querySelectorAll('.tab-item')]
    .find(e => (e.innerText || '').trim().startsWith(want));
  if (!t) return 'not-found';
  t.click();
  return 'ok';
})()"""

OPEN_DRAFTS_JS = r"""(() => {
  const e = document.querySelector('.draft-title');
  if (!e) return null;
  e.click();
  return (e.innerText || '').trim();
})()"""

DRAFT_TABS_JS = r"""(() => JSON.stringify(
  [...document.querySelectorAll('.draft-tabs .tab-item')]
    .map(e => (e.innerText || '').trim())
))()"""

DRAFT_ITEMS_JS = r"""(() => JSON.stringify(
  [...document.querySelectorAll('.draft-item')].map(e => ({
    title: (e.querySelector('.draft-title-text') || {}).innerText || null,
    saved_at: (e.querySelector('.draft-time') || {}).innerText || null,
  }))
))()"""

CLICK_DRAFT_TAB_JS = r"""(() => {
  const want = %s;
  const t = [...document.querySelectorAll('.draft-tabs .tab-item')]
    .find(e => (e.innerText || '').trim().startsWith(want));
  if (!t) return 'not-found';
  t.click();
  return 'ok';
})()"""

CLOSE_DRAWER_JS = r"""(() => {
  const m = document.querySelector('.d-drawer-mask');
  if (m) { m.click(); return 'closed'; }
  return 'no-mask';
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
            "先按 skills/rednote-post/references/publishing.md 跑它的 scripts/setup_bridge.sh，"
            "或用 XHS_SKILLS_DIR 指向仓库。")
    page = BridgePage(BRIDGE_URL)
    if not page.is_server_running():
        die("bridge server 没在跑。先在 rednote-post 里跑 `python3 scripts/xhs.py status`。")
    if not page.is_extension_connected():
        die("浏览器扩展没连上。打开装了 XHS Bridge 扩展的 Chrome 再试。")
    return page, CDPError


def jload(raw):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def cards_on_page(page) -> list[dict]:
    return jload(page.evaluate(CARD_JS)) or []


def card_key(c: dict) -> str:
    return c.get("noteId") or f"{c.get('title')}|{c.get('time')}"


def scroll_and_collect(page, limit: int,
                       settle: int = 3) -> tuple[list[dict], bool, bool]:
    """滚动收集卡片，返回 (卡片, exhausted, partial)。

    exhausted 只该在"列表确实到底"时为真——**页面还挂着「正在加载」时绝不算到底**。
    这个区别是拿 bug 换来的：列表是虚拟滚动的，首屏只挂约 10 张，往下要靠滚动
    触发下一页请求；请求迟迟不发（Chrome 窗口在后台、`document.hasFocus()` 为 false
    时很常见）卡片数就会一直不变。旧版把这种"不变"直接当成到底，
    于是静悄悄少报还报平安——2026-10-06 就漏掉了 10-07、10-08 两篇已排期笔记，
    同时输出 `scroll_exhausted: true`，据此得出的"排期有空档"结论整个是错的。
    """
    seen: dict[str, dict] = {}
    stale = 0
    settled = False
    while True:
        for c in cards_on_page(page):
            seen[card_key(c)] = c
        if len(seen) >= limit:
            break
        before = len(seen)
        page.evaluate(SCROLL_JS)
        time.sleep(1.5)
        for c in cards_on_page(page):
            seen[card_key(c)] = c
        if len(seen) == before:
            stale += 1
            if stale >= settle:
                settled = True
                break
        else:
            stale = 0
    partial = bool((jload(page.evaluate(PAGE_JS)) or {}).get("loading"))
    items = list(seen.values())
    return items[:limit], (settled and not partial), partial


def goto_note_manager(page) -> None:
    """每次扫 tab 前都重新导航。

    草稿箱在发布页，历史在笔记管理页——不重新导航就会在错误的页面上
    找 .tab-item，结果是把"页面上没有 tab"误读成"这个 tab 是空的"。
    """
    page.navigate(NOTE_MANAGER_URL)
    time.sleep(7)
    # 导航会换掉文档，focus 补丁必须重新打一次。
    page.evaluate(FOCUS_PATCH_JS)


def scan_tab(page, tab: str,
             limit: int) -> tuple[list[dict], bool, bool, list[str]]:
    tabs = jload(page.evaluate(TABS_JS)) or []
    res = page.evaluate(CLICK_TAB_JS % json.dumps(tab))
    if res == "not-found":
        # 「没找到这个 tab」不等于「这个 tab 是空的」。这里以前报 exhausted=True / partial=False，
        # 也就是把"什么都没读到"写成了"读完了、这一段是空的"。2026-10-10 23:46 那次的
        # state.json 就是它的产物：tabs 与 scheduled 都是空数组、cards_scanned 0，却报 partial: false，
        # 下游据此算出的"空档"正是已被《黑客与画家》占用的 10-11 12:30。挡住它的只有
        # plan.py ready 的「占用清单可用」那一条。读不到就是没读完——按 partial=True 报，
        # 让 warn_incomplete 与 plan.py ready 拦下来。见 rsi-reading R72。
        return [], False, True, tabs
    time.sleep(5)
    items, exhausted, partial = scroll_and_collect(page, limit)
    return items, exhausted, partial, tabs


def expected_count(tabs: list[str], tab: str) -> int | None:
    """tab 标签里写着总数（"全部 255"）。拿它当核对用的预期值。"""
    for t in tabs:
        if t.startswith(tab):
            for token in t.split():
                if token.isdigit():
                    return int(token)
    return None


def scan_queue(page, limit: int) -> dict:
    """未来定时发布 + 审核中积压。

    定时笔记既在"全部"里，也在"审核中"里（还没到发布时间就一直是审核中），
    两个 tab 各扫一次才能同时拿到"有没有定时"和"是否卡审核"。
    """
    all_cards, all_done, all_partial, tabs = scan_tab(page, "全部", limit)
    review_cards, _, review_partial, _ = scan_tab(page, "审核中", limit)
    review_ids = {c["noteId"] for c in review_cards if c.get("noteId")}

    scheduled = []
    for c in all_cards:
        if not c.get("scheduled"):
            continue
        scheduled.append({
            "noteId": c.get("noteId"),
            "title": c.get("title"),
            "time": c.get("time"),
            "in_review": c.get("noteId") in review_ids,
        })
    scheduled.sort(key=lambda x: x.get("time") or "")

    published_seen = [c for c in all_cards if not c.get("scheduled") and c.get("time")]
    page_facts = jload(page.evaluate(PAGE_JS)) or {}
    total = expected_count(tabs, "全部")
    # 有两个独立的"数据不全"来源：列表还在加载，或者拿到的卡片数明显少于标签里的总数。
    short = total is not None and total > len(all_cards) and len(all_cards) < limit
    partial = all_partial or review_partial or short
    if partial and len(all_cards) < limit and all_done:
        all_done = False
    return {
        "tabs": tabs,
        "scheduled": scheduled,
        "in_review_count": len(review_cards),
        "in_review": [{"noteId": c.get("noteId"), "title": c.get("title"),
                       "time": c.get("time")} for c in review_cards],
        "cards_scanned": len(all_cards),
        "scroll_exhausted": all_done,
        "partial": partial,
        "expected_total": total,
        "window_focused": page_facts.get("focused"),
        "recent_unpublished": published_seen[0]["time"] if published_seen else None,
    }


def scan_drafts(page) -> dict:
    """草稿箱。注意：草稿存在浏览器本地，清浏览器数据就没了。"""
    page.navigate(PUBLISH_URL)
    time.sleep(7)
    page.evaluate(FOCUS_PATCH_JS)
    opened = page.evaluate(OPEN_DRAFTS_JS)
    if not opened:
        return {"available": False,
                "reason": "找不到 .draft-title（未登录、页面改版，或不在发布页）",
                "items": [], "counts": {}}
    time.sleep(2.5)
    tabs = jload(page.evaluate(DRAFT_TABS_JS)) or []
    items = []
    for tab in tabs:
        if page.evaluate(CLICK_DRAFT_TAB_JS % json.dumps(tab)) != "ok":
            continue
        time.sleep(1.5)
        for it in (jload(page.evaluate(DRAFT_ITEMS_JS)) or []):
            it["type"] = tab.split("(")[0]
            items.append(it)
    counts = {}
    for tab in tabs:
        name = tab.split("(")[0]
        try:
            counts[name] = int(tab.split("(")[1].rstrip(")"))
        except (IndexError, ValueError):
            counts[name] = None
    page.evaluate(CLOSE_DRAWER_JS)
    return {"available": True, "entry": opened, "counts": counts, "items": items}


def scan_history(page, limit: int) -> dict:
    """已发布笔记的发布时间与五项数据，用来算账号自己的最佳时段。"""
    cards, exhausted, partial, _tabs = scan_tab(page, "已发布", limit)
    rows = []
    for c in cards:
        stats = c.get("stats") or []
        row = {"noteId": c.get("noteId"), "title": c.get("title"),
               "time": c.get("time")}
        for i, name in enumerate(STAT_ORDER):
            row[name] = stats[i] if i < len(stats) else None
        row["statsRaw"] = c.get("statsRaw")
        rows.append(row)
    rows = [r for r in rows if r.get("time")]
    rows.sort(key=lambda r: r["time"], reverse=True)
    return {"published": rows, "cards_scanned": len(cards),
            "scroll_exhausted": exhausted, "partial": partial}


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def merge_into_file(payload: dict, p: Path) -> dict:
    """把这次扫到的段并进已有文件，**不动**这次没扫的段。

    一次 `queue` 扫描只带 queue 这一段；直接覆盖会把手上的 history 抹掉，
    而下游（`rsi.py`）读 history 时用的是 `or []`——抹掉之后只会"数字变少"，
    不会报错。少报和没有，在输出上是同形的。
    """
    if not p.exists():
        return payload
    try:
        old = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return payload
    if not isinstance(old, dict):
        return payload
    return {**old, **payload}


def emit(payload: dict, out: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if out:
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = merge_into_file(payload, p)
        p.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写出 {p}")
        for k, v in merged.items():
            if isinstance(v, list):
                print(f"  {k}: {len(v)} 条")
            elif isinstance(v, dict) and k != "scanned_at":
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:120]}")
        kept = [k for k in merged if k not in payload and k != "scanned_at"]
        if kept:
            print(f"  保留本次未扫描的段：{'、'.join(kept)}")
        payload = merged
    else:
        print(text)
    warn_incomplete(payload)


def warn_incomplete(payload: dict) -> None:
    """数据不全就大声说出来。

    少报比报错危险得多：下游的"排期有空档""这里该补一篇"都建立在
    "看到的队列就是全部队列"之上，静悄悄少报会直接得出错误结论。
    警告走 stderr，免得弄脏 stdout 上的 JSON。
    """
    out = sys.stderr
    for name in ("queue", "history"):
        sec = payload.get(name)
        if not isinstance(sec, dict) or not sec.get("partial"):
            continue
        print(f"\n⚠️  {name} 扫描不完整，不能据此判断排期是否有关档：", file=out)
        print(f"    读到 {sec.get('cards_scanned')} 张卡片"
              f"，列表标签显示共 {sec.get('expected_total')} 篇。", file=out)
        if sec.get("window_focused") is False:
            print("    原因：Chrome 窗口不在前台（document.hasFocus() = false），"
                  "虚拟列表停发下一页请求。", file=out)
            print("    处理：把 Chrome 切到前台、停在笔记管理页，再重跑一次。",
                  file=out)
        else:
            print("    页面仍显示「正在加载」。把 Chrome 切到前台后重跑。", file=out)


def main() -> int:
    ap = argparse.ArgumentParser(description="只读读取小红书创作者中心状态")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("queue", "history", "all"):
        p = sub.add_parser(name)
        p.add_argument("--max", type=int, default=40, help="每个列表最多读多少张卡片")
        p.add_argument("--out", default=None, help="把 JSON 写到文件")
    p = sub.add_parser("drafts")
    p.add_argument("--out", default=None)
    args = ap.parse_args()

    page, _ = bridge()
    ts = stamp()
    payload = {"scanned_at": ts}

    if args.cmd in ("queue", "all"):
        goto_note_manager(page)
        queue = scan_queue(page, args.max)
        queue["scanned_at"] = ts
        payload["queue"] = queue
    if args.cmd in ("drafts", "all"):
        payload["drafts"] = scan_drafts(page)
    if args.cmd in ("history", "all"):
        goto_note_manager(page)
        history = scan_history(page, args.max)
        history["scanned_at"] = ts
        payload["history"] = history

    if args.cmd == "all":
        payload["source_url"] = NOTE_MANAGER_URL
    emit(payload, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
