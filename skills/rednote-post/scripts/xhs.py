#!/usr/bin/env python3
"""Drive 小红书 (RedNote) publishing through the browser-extension bridge.

Prerequisites (see references/publishing.md):
  * a checkout of autoclaw-cc/xiaohongshu-skills, path in XHS_SKILLS_DIR
  * its venv deps installed, with websockets pinned to 13.1
  * the XHS Bridge Chrome extension loaded and enabled, logged in

Subcommands:
  status                      bridge + extension + login state
  reset                       open a clean publish page (clears any previous fill)
  fill     --dir D            upload images + title + body from the post dir
  tags     --dir D            (re)commit the topic tags from the body's last line
  collection NAME             attach the note to a 合集
  verify   [--dir D]          read the form back and compare against local files
  publish                     click 发布
  check-published TITLE       confirm it landed in 已发布 on the note manager
  delete-note --id N --yes    delete one note by noteId (scheduled ones included)
  verify-scheduled --title T --expect "YYYY-MM-DD HH:MM"
                              read the time the platform actually stored
  shot     [--out PATH]       screenshot Chrome (for showing the user)

Exit codes: 0 ok, 1 failed check, 2 environment/permission problem.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("XHS_SKILLS_DIR", "/tmp/xhs-skills-repo"))
BRIDGE_URL = os.environ.get("XHS_BRIDGE_URL", "ws://localhost:9333")
PUBLISH_URL = ("https://creator.xiaohongshu.com/publish/publish"
               "?source=official&target=image")
NOTE_MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"

TITLE_SEL = "div.d-input input"
EDITOR_SEL = "[role='textbox']"
PREVIEW_SEL = ".img-preview-area .pr"
COLLECTION_BTN = ".collection-plugin-button"

# The topic-suggestion popover is a tippy panel; its results list carries the id
# `creator-editor-topic-container`. Its contents can be stale: it has been seen
# holding the previous query's results (because the search silently failed), and
# a parked instance with the default hot list sits at the page's top-left. So
# neither "the node exists" nor "it has .item children" proves the list is good.
TOPIC_BOX_SEL = "#creator-editor-topic-container"
TOPIC_ITEM_SEL = f"{TOPIC_BOX_SEL} .item"
TOPIC_COUNT_JS = ("document.querySelectorAll(\"[role='textbox'] .tiptap-topic\")"
                  ".length")

# Only click a topic when the item matches the query we just typed *and* its
# on-screen centre is really the topmost element there -- the same hit-test a
# real mouse click performs. That combination rejects stale and parked lists.
TAG_FIND_JS = """(() => {
  const q = %s;
  const box = document.getElementById('creator-editor-topic-container');
  if (!box) return null;
  const items = [...box.querySelectorAll('.item')];
  for (let i = 0; i < items.length; i++) {
    const el = items[i];
    // 下拉项形如 `#读书笔记1.2亿浏览`：话题名后面紧跟浏览数。用 includes
    // 会把「创作」选成「创作者体验挽回」——必须要求名字在下一个汉字/字母
    // 之前就结束，选不到就宁可不选（verify 会拦住话题数不符）。
    const t = (el.textContent || '').trim();
    const at = t.indexOf('#' + q);
    if (at < 0) continue;
    const rest = t.slice(at + 1 + q.length);
    if (rest && /[\u4e00-\u9fa5A-Za-z]/.test(rest[0])) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === el || el.contains(hit) || hit.contains(el))) continue;
    return JSON.stringify({index: i, text: t, x: x, y: y});
  }
  return null;
})()"""

# A trailing line of `#a #b #c` is how the body files carry the topics.
TAG_LINE_RE = re.compile(r"^(?:#[^\s#]+\s*)+$")

# 平台还没有这个词的时候，下拉里的第一项是 `#诺贝尔文学奖新建话题`。**默认不点它**：
# 建一个新话题等于开一个没有内容、没有流量的空话题页，作者多半不是这个意思。
# 只有调用方明确要求（环境变量 `XHS_NEW_TOPIC=1`）时才点——那是"这个词平台确实没有，
# 我就要它"的显式决定（用户 2026-10-09 对 `#诺贝尔文学奖` 就是这么说的）。
TAG_NEW_JS = """(() => {
  const q = %s;
  const box = document.getElementById('creator-editor-topic-container');
  if (!box) return null;
  for (const el of box.querySelectorAll('.item')) {
    if ((el.textContent || '').trim() !== '#' + q + '新建话题') continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) continue;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === el || el.contains(hit) || hit.contains(el))) continue;
    return JSON.stringify({text: (el.textContent || '').trim(), x: x, y: y});
  }
  return null;
})()"""

# Move the caret to the end of the body and open a fresh paragraph for the tags.
INSERT_TAG_LINE_JS = """(() => {
  const el = document.querySelector(%s);
  if (!el) return 'no-editor';
  el.focus();
  const range = document.createRange();
  range.selectNodeContents(el);
  range.collapse(false);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  document.execCommand('insertParagraph', false, null);
  return 'ok';
})()"""

# Delete a `#tag` that never became a topic so it can be retyped. Uses the
# browser's own delete command on the live caret -- deliberately no focus() or
# selection writes, which are what desync the suggestion component.
RETRACT_TAG_JS = """(() => {
  const el = document.querySelector(%s);
  if (!el) return 'no-editor';
  const txt = (el.innerText || '').replace(/\\u00a0/g, ' ');
  const want = %s;
  if (!txt.replace(/\\s+$/, '').endsWith(want)) return 'not-tail';
  for (let i = 0; i < %d; i++) document.execCommand('delete', false, null);
  return 'deleted';
})()"""


def reexec_in_venv() -> None:
    """The bridge needs `websockets`, which lives in the skills-repo venv.

    Re-exec there so callers can just run `python3 scripts/xhs.py ...` with any
    interpreter instead of having to remember which python to use.
    """
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

READBACK_JS = """JSON.stringify({
  title: (document.querySelector(%s)||{}).value || '',
  body: ((document.querySelector(%s)||{}).innerText || ''),
  previews: document.querySelectorAll(%s).length,
  topics: document.querySelectorAll("[role='textbox'] .tiptap-topic").length,
  collection: (document.querySelector('.collection-plugin-wrapper')||{}).innerText || '',
  url: location.href
})""" % (json.dumps(TITLE_SEL), json.dumps(EDITOR_SEL),
         json.dumps(PREVIEW_SEL))


# 笔记管理页的每张卡把 noteId 藏在 data-impression 的 JSON 里——这是唯一稳定的
# 定位手段：标题会重复（同一条笔记替换后会短暂共存两份），顺序也会变。
NOTE_CARDS_JS = """(() => {
  const out = [];
  for (const c of document.querySelectorAll('.note-card')) {
    let id = '';
    try {
      id = JSON.parse(c.getAttribute('data-impression') || '{}')
             .noteTarget.value.noteId;
    } catch (e) {}
    out.push({id: id, text: (c.textContent || '').replace(/\\s+/g, ' ').trim()});
  }
  return JSON.stringify(out);
})()"""

# 卡片上的定时/发布时间。**这是唯一可信的来源**：`schedule` 回读的是表单输入框的
# value，而平台在提交时会自己重算一遍——超出窗口（实测：今天 + 13 天）就把你填的时刻
# 丢掉，退回它自己的默认值「今天 + 1 小时、向上取到 5 分钟」。2026-10-07 因此把
# 2026-10-21 22:00 的档排成了当天 22:45，而表单回读一路都显示 10-21 22:00。
# 所以"到底排上了没有"只能回读卡片，不能信表单。
CARD_TIME_JS = """(() => JSON.stringify(
  [...document.querySelectorAll('.note-card')].map(c => {
    let id = '';
    try {
      id = JSON.parse(c.getAttribute('data-impression') || '{}')
             .noteTarget.value.noteId;
    } catch (e) {}
    const t = (s) => ((c.querySelector(s) || {}).innerText || '').trim();
    return {id: id, title: t('.note-card__title'), time: t('.note-card__time'),
            scheduled: !!c.querySelector('.note-card__schedule')};
  })
))()"""

# 删除确认弹窗在 Vue portal 里，而且**关闭后仍留在 DOM**（走 opacity 淡出），
# 所以要能认出"这次真的弹出来了"。注意：不能用 offsetParent 判可见——这个
# 容器是 position:fixed，offsetParent 恒为 null，用它判会永远跳过弹窗。
# 确认按钮是 .confirm-button；只靠 button.click() 在这套组件上不稳，实测
# 要点到真鼠标坐标才吃得准。
VISIBLE_FN_JS = """const visible = (el) => {
  if (!el.getClientRects().length) return false;
  const s = getComputedStyle(el);
  return s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0.05;
};"""

DELETE_MODAL_JS = ("(() => {" + VISIBLE_FN_JS + """
  for (const m of document.querySelectorAll('.d-modal.modal-container')) {
    if (!visible(m)) continue;
    const btn = m.querySelector('button.confirm-button');
    if (!btn) continue;
    const r = btn.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    return JSON.stringify({
      text: (m.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
      x: r.left + r.width / 2, y: r.top + r.height / 2});
  }
  return '{}';
})()""")


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
    return BridgePage(BRIDGE_URL), CDPError


# 命令墙钟上限。服务端在**进程启动时**把它定死，所以一个用旧默认起着的
# 常驻 server 会把之后所有 CLI 都按在旧上限上——调 CLI 这边的环境变量没用。
# 这里的做法是：发现端口上那个 server 的上限不够，就把它换掉再起一个。
DEFAULT_CMD_TIMEOUT = 900


def _kill_port_owner() -> bool:
    """把占着 bridge 端口的旧 server 停掉（换一个上限更高的）。

    只杀命令行里带 `bridge_server.py` 的进程——`lsof -ti tcp:PORT` 也会列出
    只是"连着"这个端口的客户端，误杀会连自己一起带走。
    """
    port = BRIDGE_URL.rsplit(":", 1)[-1].split("/")[0] or "9333"
    try:
        out = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    killed = False
    for pid in [p.strip() for p in out.split() if p.strip().isdigit()]:
        if int(pid) == os.getpid():
            continue
        try:
            cmd = subprocess.run(["ps", "-o", "command=", "-p", pid],
                                 capture_output=True, text=True, timeout=10).stdout
        except Exception:
            continue
        if "bridge_server.py" not in cmd:
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
            print(f"停掉旧 bridge server（pid {pid}，命令上限不足）")
            killed = True
        except ProcessLookupError:
            pass
    if killed:
        time.sleep(2)
    return killed


def _wait_extension(page, timeout: float = 25.0) -> bool:
    """等扩展重新连上刚换过的 server（扩展侧是 3s 一次重连）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if page.is_extension_connected():
            return True
        time.sleep(1)
    return False


def _spawn_server(python: str, server, timeout: float) -> None:
    env = dict(os.environ)
    env["XHS_CMD_TIMEOUT"] = str(int(timeout))
    subprocess.Popen([python, str(server)], env=env,
                     stdout=open("/tmp/xhs-bridge.log", "ab"),
                     stderr=subprocess.STDOUT)


def ensure_server(required_timeout: float = DEFAULT_CMD_TIMEOUT) -> None:
    page, _ = bridge()
    restarted = False
    if page.is_server_running():
        live = page.server_cmd_timeout()
        if live is None or live >= required_timeout:
            return
        # 端口上是一个用更小上限起着的常驻进程：CLI 侧调大没用，必须换掉。
        restarted = _kill_port_owner()
    server = SKILLS_DIR / "scripts" / "bridge_server.py"
    if not server.exists():
        die(f"找不到 {server}；先跑 scripts/setup_bridge.sh")
    print("启动 bridge server…")
    venv = SKILLS_DIR / ".venv" / "bin" / "python"
    python = str(venv) if venv.exists() else sys.executable
    _spawn_server(python, server, required_timeout)
    for _ in range(10):
        time.sleep(1)
        if page.is_server_running():
            break
    else:
        die("bridge server 起不来，见 /tmp/xhs-bridge.log")
    if restarted and not _wait_extension(page):
        # 换过 server 之后扩展要重连，不等它就会在第一条命令上撞
        # 「Extension 未连接」——这正是换 server 自带的新故障。
        die("换了 bridge server，但浏览器扩展 25s 内没重连上；"
            "看看 Chrome 里 XHS Bridge 扩展是否还开着")


def cli(*args: str) -> subprocess.CompletedProcess:
    venv = SKILLS_DIR / ".venv" / "bin" / "python"
    python = str(venv) if venv.exists() else sys.executable
    return subprocess.run([python, str(SKILLS_DIR / "scripts" / "cli.py"), *args],
                          capture_output=True, text=True, errors="replace")


def readback(page) -> dict:
    got = json.loads(page.evaluate(READBACK_JS))
    got["bodyLen"] = len(got.get("body", ""))
    return got


def normalize(text: str) -> str:
    """Drop hashtags and all whitespace.

    The filler pulls trailing `#tags` out of the body and re-adds them as topic
    chips, and the editor re-wraps lines, so raw lengths never match.
    """
    return re.sub(r"\s+", "", re.sub(r"#[^\s#]+", "", text or ""))


def split_tags(body: str) -> tuple[list[str], str]:
    """Split a trailing `#a #b #c` line off the body.

    The upstream CLI extracts that line itself and re-enters the tags with a JS
    `.click()` on the topic dropdown. That click is not `isTrusted`, so tiptap's
    topic handler ignores it and the text stays inert -- which is how a post
    ends up with only its last hashtag as a real topic. We take the line out
    ourselves and commit the topics with a trusted click instead.
    """
    lines = body.rstrip().split("\n")
    if lines:
        last = lines[-1].strip()
        if TAG_LINE_RE.match(last):
            seen: set[str] = set()
            tags: list[str] = []
            for t in re.findall(r"#([^\s#]+)", last):
                if t not in seen:
                    seen.add(t)
                    tags.append(t)
            return tags, "\n".join(lines[:-1]).rstrip()
    return [], body


def topic_count(page) -> int:
    got = page.evaluate(TOPIC_COUNT_JS)
    return int(got) if got else 0


def chip_texts(page) -> list[str]:
    """The committed topic chips, e.g. ['#读书笔记[话题]#', ...]."""
    got = page.evaluate("JSON.stringify([...document.querySelectorAll("
                        "\"[role='textbox'] .tiptap-topic\")]"
                        ".map(e => (e.innerText || '').trim()))")
    return json.loads(got) if got else []


def chip_names(page) -> list[str]:
    """Chip texts with the `#…[话题]#` wrapper stripped, e.g. ['读书笔记', ...]."""
    out = []
    for t in chip_texts(page):
        n = t.strip().lstrip("#")
        if n.endswith("[话题]#"):
            n = n[: -len("[话题]#")]
        out.append(n.strip())
    return out


def commit_tag(page, tag: str, timeout: float = 4.0) -> bool:
    """Turn the in-progress `#tag` into a real topic chip.

    Requires a *trusted* click: `page.click_element` dispatches a synthetic
    `el.click()`, which the page's tiptap topic handler discards. The bridge's
    `click_nth_element` goes through CDP `Input.dispatchMouseEvent` instead,
    and that works. Each click is verified against the topic count and retried,
    because a click can also land without taking effect.
    """
    debug = os.environ.get("XHS_DEBUG")
    deadline = time.monotonic() + timeout
    tries = 0
    while time.monotonic() < deadline and tries < 4:
        found = page.evaluate(TAG_FIND_JS % json.dumps(tag))
        if not found:
            if debug:
                print(f"    [{tag}] 下拉未就绪", file=sys.stderr)
            time.sleep(0.25)
            continue
        tries += 1
        item = json.loads(found)
        try:
            # Click by the hit-tested coordinates, not by list index: the list
            # re-renders between the probe and the click, so `click_nth_element`
            # has been seen committing a *different* topic than the one matched
            # (「创作」came out as「创作者体验挽回」).
            page.mouse_click(item["x"], item["y"])
        except Exception as exc:
            if debug:
                print(f"    [{tag}] 点击异常 {exc}", file=sys.stderr)
            time.sleep(0.3)
            continue
        time.sleep(0.8)
        want = f"#{tag}[话题]#"
        if want in chip_texts(page):
            return True
        if debug:
            print(f"    [{tag}] 点了但没生效（第 {tries} 次，页面 "
                  f"{chip_texts(page)[-2:]}）", file=sys.stderr)
        time.sleep(0.3)

    # 精确话题不存在时（平台没有这个词），显式要求才去点「新建话题」。
    if os.environ.get("XHS_NEW_TOPIC"):
        found = page.evaluate(TAG_NEW_JS % json.dumps(tag))
        if found:
            item = json.loads(found)
            try:
                page.mouse_click(item["x"], item["y"])
            except Exception:
                return False
            time.sleep(1.0)
            if f"#{tag}[话题]#" in chip_texts(page):
                return True
            if debug:
                print(f"    [{tag}] 新建话题点了也没生效", file=sys.stderr)
    return False


def editor_text(page) -> str:
    return page.evaluate("(document.querySelector(%s)||{}).innerText||''" % json.dumps(EDITOR_SEL)) or ""


def retract_tag(page, tag: str) -> bool:
    """Remove an inert `#tag`, but only if it is genuinely the tail."""
    before = len(editor_text(page))
    got = page.evaluate(RETRACT_TAG_JS % (json.dumps(EDITOR_SEL),
                                          json.dumps("#" + tag), len(tag) + 1))
    time.sleep(0.3)
    return got == "deleted" and len(editor_text(page)) == before - (len(tag) + 1)


def enter_tags(page, tags: list[str], attempts: int = 2,
               max_consecutive_failures: int = 2) -> tuple[list[str], list[str]]:
    """Append the tags as real topic chips; returns (committed, failed).

    Deliberately does not touch focus or the selection between tags: XHS's
    suggestion component desyncs if something focuses the editor mid-flow, and
    then it keeps serving the previous query's list and never refreshes.

    When the dropdown is broken, every tag fails and each attempt costs seconds
    of retyping; stop after a couple of consecutive failures so we neither waste
    a minute nor hammer a failing endpoint. The leftovers stay visible in the
    editor and `verify` will flag them.
    """
    page.evaluate(INSERT_TAG_LINE_JS % json.dumps(EDITOR_SEL))
    time.sleep(0.8)

    committed: list[str] = []
    failed: list[str] = []
    consecutive = 0
    for i, tag in enumerate(tags):
        ok = False
        for attempt in range(attempts):
            page.type_text("#", delay_ms=0)
            time.sleep(0.2)
            for char in tag:
                page.type_text(char, delay_ms=0)
                time.sleep(0.05)
            if commit_tag(page, tag):
                ok = True
                break
            # The dropdown sometimes never populates for one query; retype it.
            if not retract_tag(page, tag):
                break
            time.sleep(0.4)
        if ok:
            committed.append(tag)
            consecutive = 0
        else:
            # Terminate the inert text so it does not merge into the next tag.
            page.type_text(" ", delay_ms=0)
            failed.append(tag)
            consecutive += 1
            if consecutive >= max_consecutive_failures:
                failed.extend(tags[i + 1:])
                break
        time.sleep(0.4)
    return committed, failed


# --------------------------------------------------------------------------- commands

def cmd_status(_args) -> int:
    page, _ = bridge()
    ok_server = page.is_server_running()
    ok_ext = page.is_extension_connected() if ok_server else False
    print(f"bridge server : {'运行中' if ok_server else '未运行'}")
    if ok_server:
        live = page.server_cmd_timeout()
        if live is None:
            print("命令上限      : 未知（旧版 server，没有报这个字段）")
        else:
            flag = "" if live >= DEFAULT_CMD_TIMEOUT else \
                f"  ← 低于 {DEFAULT_CMD_TIMEOUT}s，长正文会撞 R36，跑任意命令会自动换掉它"
            print(f"命令上限      : {int(live)}s{flag}")
    print(f"浏览器扩展    : {'已连接' if ok_ext else '未连接'}")
    if ok_ext:
        print(f"当前页面      : {page.evaluate('location.href')}")
    else:
        print("\n按 references/publishing.md 配置：\n"
              f"  bash {Path(__file__).parent}/setup_bridge.sh")
    return 0 if (ok_server and ok_ext) else 2


def cmd_reset(_args) -> int:
    page, _ = bridge()
    page.navigate(PUBLISH_URL)
    time.sleep(6)
    print("已打开干净的发布页：", json.loads(readback_json(page))["url"])
    return 0


def readback_json(page) -> str:
    return page.evaluate(READBACK_JS)


def cmd_fill(args) -> int:
    page, _ = bridge()
    root = Path(args.dir).resolve()
    title, body = root / args.title, root / args.body
    images = resolve_images(root)
    for path in (title, body):
        if not path.exists():
            die(f"缺少 {path}")
    if not images:
        die(f"{root} 下没有图片（先跑 render_cards.py）")

    t = title.read_text(encoding="utf-8").strip()
    if len(t) > 20:
        die(f"标题 {len(t)} 字，超过 20 字上限：{t}")
    b = body.read_text(encoding="utf-8").strip()
    if len(b) > 1000:
        print(f"警告：正文 {len(b)} 字，超过 1000 字上限", file=sys.stderr)

    tags, clean_body = split_tags(b)

    # Hand the CLI a body with no hashtags: its own tag routine clicks the
    # dropdown with an untrusted JS click, which tiptap ignores, and the
    # leftovers come out as inert text. We commit the topics ourselves below.
    fd, tmp_body = tempfile.mkstemp(prefix="xhs-body-", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(clean_body)
    try:
        res = cli("fill-publish", "--title-file", str(title), "--content-file", tmp_body,
                  "--images", *[str(p) for p in images])
    finally:
        os.unlink(tmp_body)
    print(res.stdout[-2000:])
    if "表单已填写" not in res.stdout:
        print(res.stderr[-1500:], file=sys.stderr)
        die("填写未完成")

    if tags:
        committed, failed = enter_tags(page, tags)
        print(f"标签：{len(committed)}/{len(tags)} 生效" +
              (f"，失败 {failed}" if failed else ""))
        if failed:
            print("警告：以上标签没能变成话题。它们现在是编辑器里的普通文字，"
                  "请在页面上手动点成话题，或稍后重跑 `xhs.py tags`；"
                  "确认前不要发布。", file=sys.stderr)

    if args.collection:
        rc = cmd_collection(argparse.Namespace(name=args.collection))
        if rc:
            return rc
    return 1 if (tags and failed) else 0


def cmd_tags(args) -> int:
    """(Re)commit topic tags from the body file's trailing `#a #b #c` line."""
    page, _ = bridge()
    root = Path(args.dir).resolve()
    tags, _ = split_tags((root / args.body).read_text(encoding="utf-8").strip())
    if args.tags:
        tags = [t.lstrip("#") for t in args.tags]
    if not tags:
        die(f"{root / args.body} 末尾没有 `#a #b #c` 标签行，也没有传 --tags")

    before = topic_count(page)
    committed, failed = enter_tags(page, tags)
    after = topic_count(page)
    print(f"标签：{len(committed)}/{len(tags)} 生效（页面话题数 {before} → {after}）")
    if failed:
        print(f"未生效：{failed}", file=sys.stderr)
        return 1
    return 0


def resolve_images(root: Path) -> list[Path]:
    """Pick the images to upload, in order.

    Prefer manifest.json's card list: a blanket `*.jpg` glob silently picks up
    stale renders (and duplicates the whole set) as soon as a directory holds
    more than one naming scheme.
    """
    mf = root / "manifest.json"
    if mf.exists():
        try:
            cards = json.loads(mf.read_text(encoding="utf-8")).get("cards") or []
        except json.JSONDecodeError:
            cards = []
        picked = []
        for c in cards:
            for ext in (".jpg", ".png"):
                p = root / f"{c['out']}{ext}"
                if p.exists():
                    picked.append(p)
                    break
            else:
                die(f"manifest 里的 {c['out']} 还没渲染，先跑 render_cards.py")
        if picked:
            return picked

    for pattern in ("[0-9][0-9]-*.jpg", "[0-9]-*.jpg"):
        found = sorted(root.glob(pattern))
        if found:
            return found
    return sorted(root.glob("*.jpg"))


def cmd_collection(args) -> int:
    page, _ = bridge()
    name = args.name
    current = page.evaluate(
        "(document.querySelector('.collection-plugin-wrapper')||{}).innerText || ''")
    if name in current:
        print(f"合集已是「{name}」")
        return 0

    if not page.has_element(COLLECTION_BTN):
        die("找不到「选择合集」按钮——页面结构可能改版了")
    page.click_element(COLLECTION_BTN)
    time.sleep(2)

    clicked = page.evaluate("""(() => {
      const el = [...document.querySelectorAll('.item-label,.item-content,.item')]
        .find(e => e.textContent.trim() === %s);
      if (!el) return 'missing';
      const t = el.closest('.item') || el;
      t.scrollIntoView({block:'center'});
      const r = t.getBoundingClientRect(), x = r.left + r.width/2, y = r.top + r.height/2;
      // These are custom components: a bare .click() is often ignored.
      ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(k =>
        t.dispatchEvent(new MouseEvent(k, {bubbles:true, cancelable:true, clientX:x, clientY:y})));
      return 'clicked';
    })()""" % json.dumps(name))
    time.sleep(2)

    if clicked == "missing":
        die(f"合集列表里没有「{name}」。可用合集：\n" + page.evaluate(
            "[...document.querySelectorAll('.collection-plugin-wrapper .item-label')]"
            ".map(e=>e.textContent.trim()).join('\\n')"))
    after = page.evaluate(
        "(document.querySelector('.collection-plugin-wrapper')||{}).innerText || ''")
    if name not in after:
        die(f"点了「{name}」但没生效，当前：{after.strip()[:80]}")
    print(f"已加入合集「{name}」")
    return 0


# ------------------------------------------------------------------ 更多设置
#
# 「定时发布」「原创声明」都是自定义开关组件：对 .d-switch-simulator 调
# el.click() 不生效，要用 CDP 真点击打中心点。稳定锚点只有标签文字本身——
# 外层 class 名是每次构建都会变的哈希（实测见过 .custom-date-picker-44）。

SWITCH_PROBE_JS = """(() => {
  const name = %s;
  const lbl = [...document.querySelectorAll('span,div')].find(
    el => (el.innerText || '').trim() === name && el.children.length === 0);
  if (!lbl) return JSON.stringify({err: 'no-label'});
  const wrap = lbl.closest('.custom-switch-wrapper')
            || lbl.parentElement.parentElement;
  const sw = wrap.querySelector('.d-switch-simulator') || wrap.querySelector('.d-switch');
  if (!sw) return JSON.stringify({err: 'no-switch'});
  sw.scrollIntoView({block: 'center'});
  const r = sw.getBoundingClientRect();
  const cls = String(sw.className || '');
  return JSON.stringify({
    on: cls.split(/\\s+/).includes('checked') && !cls.includes('unchecked'),
    x: r.left + r.width / 2, y: r.top + r.height / 2
  });
})()"""


def switch_state(page, label: str) -> dict:
    return json.loads(page.evaluate(SWITCH_PROBE_JS % json.dumps(label)))


# R35: a confirmed dialog whose Vue leave-transition deadlocks parks an
# *invisible* `.d-modal-mask` (display:block / opacity:0) over the page forever,
# and that mask swallows every click aimed at the form behind it. Observed
# 2026-10-05 and again 2026-10-06, both times on the 原创声明须知 dialog.
# An invisible mask has no legitimate job, so drop it (and the modal that was
# leaving with it). A mask that is still visible belongs to a real dialog —
# leave that alone and keep waiting.
CLEAR_STUCK_MASK_JS = """(() => {
  const mask = document.querySelector('.d-modal-mask');
  if (!mask) return 0;
  const cs = getComputedStyle(mask);
  if (cs.display === 'none') return 0;
  if (parseFloat(cs.opacity || '1') > 0.01) return 0;
  mask.remove();
  let n = 1;
  document.querySelectorAll('.d-modal').forEach((m) => { m.remove(); n++; });
  return n;
})()"""


def wait_no_mask(page, timeout: float = 45.0) -> None:
    """Wait out any modal mask.

    The 原创声明须知 modal parks a full-viewport `.d-modal-mask` over the page
    for a while after it closes, and that mask swallows every click aimed at
    the form behind it — clicking 定时发布 while one is up silently does
    nothing.

    Measured 2026-10: the mask stays in the DOM with `display:block` and
    `opacity:0` for ~18s after the 原创声明 dialog is confirmed. An earlier
    10s budget expired first, the click was swallowed, and 定时发布 looked
    like a broken switch. So: wait generously.

    If it is *still* there when the budget runs out, it is not slow — it is
    stuck (R35). Self-heal by removing the invisible overlay instead of dying:
    the Vue state survives, the form is intact, and re-running the command
    costs nothing. Dying here used to drag down the unrelated `schedule` step
    that runs next.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not page.evaluate("!!document.querySelector('.d-modal-mask')"):
            return
        time.sleep(0.4)
    removed = page.evaluate(CLEAR_STUCK_MASK_JS)
    if removed:
        print(f"弹窗遮罩超过 {timeout:.0f}s 未消失，已清掉卡死的遮罩节点"
              f"（{removed} 个），继续。", file=sys.stderr)
        return
    die(f"弹窗遮罩 .d-modal-mask 超过 {timeout:.0f}s 仍未消失，"
        "且它不是透明残留（真弹窗还开着），此时点击会被吞掉。请稍后重跑这条命令。")


def set_switch(page, label: str, want: bool = True) -> bool:
    """Flip a 更多设置 switch, verifying by re-reading rather than trusting the click."""
    wait_no_mask(page)
    info = switch_state(page, label)
    if info.get("err"):
        die(f"找不到「{label}」开关：{info['err']}（页面可能改版）")
    if bool(info.get("on")) != want:
        page.mouse_click(info["x"], info["y"])
        time.sleep(2)
        info = switch_state(page, label)
    on = bool(info.get("on"))
    if on != want:
        die(f"「{label}」开关没能切到{'开' if want else '关'}")
    return on


def cmd_original(_args) -> int:
    """打开原创声明，并处理「原创声明须知」弹窗。"""
    page, _ = bridge()
    set_switch(page, "原创声明", True)
    time.sleep(0.8)
    # 勾选框是自定义组件：input.click() 能改 checked，但 Vue 要等一拍才把
    # 「声明原创」按钮解禁——同一帧里读按钮会读到 disabled，误判成失败。
    step = "no-dialog"
    for _ in range(5):
        step = page.evaluate("""(() => {
          const footers = [...document.querySelectorAll('div.footer')];
          const f = footers.find(x => (x.innerText || '').includes('原创声明须知'));
          if (!f) return 'no-dialog';
          const cb = f.querySelector('div.d-checkbox input[type="checkbox"]');
          if (cb && !cb.checked) { cb.click(); return 'checking'; }
          const btn = f.querySelector('button.custom-button') || f.querySelector('button');
          if (!btn) return 'no-button';
          if (btn.disabled || btn.classList.contains('disabled')) return 'waiting';
          btn.click();
          return 'confirmed';
        })()""")
        if step in ("no-dialog", "confirmed"):
            break
        time.sleep(0.5)
    if step in ("confirmed", "no-dialog"):
        time.sleep(0.5)
        wait_no_mask(page)
        info = switch_state(page, "原创声明")
        print(f"原创声明：{'已开启' if info.get('on') else '未开启'}（弹窗 {step}）")
        return 0 if info.get("on") else 1
    die(f"原创声明确认弹窗没能通过：{step}（勾选框或按钮没就绪）")


def cmd_schedule(args) -> int:
    """打开定时发布开关并写入时间。args.at 形如 2026-10-06 21:00。"""
    page, _ = bridge()
    target = args.at.strip()
    try:
        when = datetime.strptime(target, "%Y-%m-%d %H:%M")
    except ValueError:
        die(f"时间格式应为 'YYYY-MM-DD HH:MM'，收到 {target!r}")
    if when <= datetime.now() + timedelta(minutes=30):
        die(f"定时时间 {target} 距现在不足 30 分钟或已过")

    set_switch(page, "定时发布", True)
    time.sleep(1)

    # 别用 `.post-time-wrapper input`：那里面第一个 input 是开关自己的
    # checkbox，点它等于把定时发布又关掉。日期框要按 datepicker 祖先找。
    sel = page.evaluate("""(() => {
      // 清掉上一次留下的标记：否则 querySelector 会命中还在 DOM 里的旧元素
      // （开关自己的 checkbox 就曾被打过标记，点它等于把定时发布关掉）。
      document.querySelectorAll('[data-xhs-time-input]')
        .forEach(el => el.removeAttribute('data-xhs-time-input'));
      const i = [...document.querySelectorAll('input.d-text')].find(
        el => el.closest('[class*=datepicker]')
              && el.getBoundingClientRect().height > 0);
      if (!i) return '';
      i.setAttribute('data-xhs-time-input', '1');
      return 'input[data-xhs-time-input]';
    })()""")
    if not sel:
        die("找不到定时时间输入框——页面可能改版")

    pos = json.loads(page.evaluate(
        "(() => { const i = document.querySelector(%s);"
        "i.scrollIntoView({block:'center'});"
        "const r = i.getBoundingClientRect();"
        "return JSON.stringify({x:r.left+r.width/2, y:r.top+r.height/2}); })()"
        % json.dumps(sel)))
    page.mouse_click(pos["x"], pos["y"])
    time.sleep(0.5)
    page.select_all_text(sel)
    time.sleep(0.3)
    page.type_text(target, delay_ms=40)
    time.sleep(0.6)
    page.press_key("Tab")
    time.sleep(1.5)

    read = lambda: page.evaluate(
        "(document.querySelector(%s)||{}).value || ''" % json.dumps(sel))
    got = read()
    if got != target:
        # 富交互日期组件有时吞掉输入法式键入，用原生 setter 兜底再触发事件。
        page.evaluate("""(() => {
          const i = document.querySelector(%s);
          const set = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
          set.call(i, %s);
          ['input', 'change'].forEach(k =>
            i.dispatchEvent(new Event(k, {bubbles: true})));
        })()""" % (json.dumps(sel), json.dumps(target)))
        time.sleep(1)
        page.press_key("Tab")
        time.sleep(1)
        got = read()

    if got != target:
        die(f"定时时间写入失败：页面读到 {got!r}，期望 {target!r}")

    on = switch_state(page, "定时发布")
    print(f"定时发布：{'已开启' if on.get('on') else '未开启'}，时间 {got}")
    return 0



def cmd_verify(args) -> int:
    page, _ = bridge()
    got = readback(page)
    print(json.dumps(got, ensure_ascii=False, indent=2))

    problems = []
    if args.dir:
        root = Path(args.dir).resolve()
        want_title = (root / args.title).read_text(encoding="utf-8").strip()
        raw_body = (root / args.body).read_text(encoding="utf-8")
        want_tags, want_body = split_tags(raw_body.strip())
        want_body = normalize(want_body)
        got_body = normalize(got.get("body", ""))
        want_imgs = len(resolve_images(root))

        if got["title"] != want_title:
            problems.append(f"标题不一致：页面「{got['title']}」 vs 本地「{want_title}」")
        if want_body and not got_body.startswith(want_body[:30]):
            problems.append("正文开头对不上，页面上可能不是这一篇")
        slack = max(60, int(0.15 * len(want_body)))
        if abs(len(got_body) - len(want_body)) > slack:
            problems.append(
                f"正文字数偏差过大：页面 {len(got_body)} vs 本地 {len(want_body)}"
                f"（去标签后，容差 {slack}）")
        if got["previews"] != want_imgs:
            problems.append(f"图片张数不一致：页面 {got['previews']} vs 本地 {want_imgs}")
        if want_tags and got.get("topics", 0) != len(want_tags):
            problems.append(
                f"话题标签没全部生效：页面 {got.get('topics', 0)} 个 vs 本地 {len(want_tags)} 个"
                f"（{ '、'.join(want_tags) }）")
        elif want_tags:
            # 数对了不代表对：话题词平台没有时，真点击会落到下拉里已有的那条上，
            # 于是多出一个**重复**的话题、少掉你要的那个——个数仍然是对的。
            # 所以再比一次文本（多重集比较，顺序不管）。
            want = [t.lstrip("#") for t in want_tags]
            chips = chip_names(page)
            if sorted(chips) != sorted(want):
                problems.append(
                    f"话题文本对不上：页面 {chips} vs 本地 {want}"
                    "（多半是某个话题词平台没有、点击落到了别的项上；"
                    "加 XHS_NEW_TOPIC=1 重填）")
    if args.collection and args.collection not in got["collection"]:
        problems.append(f"合集不是「{args.collection}」：{got['collection'].strip()[:60]}")

    for p in problems:
        print(f"  ✗ {p}", file=sys.stderr)
    print("回读一致" if not problems else "回读不一致", file=sys.stderr)
    return 1 if problems else 0


def cmd_publish(_args) -> int:
    res = cli("click-publish")
    print(res.stdout[-1200:])
    page, _ = bridge()
    time.sleep(3)
    got = readback(page)
    published = "published=true" in got["url"] or (got["previews"] == 0 and not got["title"])
    print("页面状态：", json.dumps(got, ensure_ascii=False))
    if published:
        print("已提交（URL/表单状态确认）")
    else:
        print("页面没有出现已提交的迹象——手动看一眼再决定是否重试", file=sys.stderr)
    return 0 if published else 1


def cmd_check_published(args) -> int:
    page, _ = bridge()
    page.navigate(NOTE_MANAGER_URL)
    time.sleep(6)
    found = page.evaluate(
        "JSON.stringify({has:(document.body.innerText||'').includes(%s)})"
        % json.dumps(args.title))
    if not json.loads(found)["has"]:
        print("未在笔记管理中看到该标题。若刚发布，等一下再查。", file=sys.stderr)
        return 1
    print(f"「{args.title}」已在笔记管理中")
    return 0


def cmd_verify_scheduled(args) -> int:
    """回读笔记卡片上的定时时间，和期望值比对。

    表单回读会骗人（见 CARD_TIME_JS 的注释）：超窗的时刻，`schedule` 读到的是你填的
    值，平台存下的却是它自己的默认值。这里读的是卡片——平台存了什么就是什么。
    """
    page, _ = bridge()
    try:
        want = datetime.strptime(args.expect.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        die(f"时间格式应为 'YYYY-MM-DD HH:MM'，收到 {args.expect!r}")

    page.navigate(NOTE_MANAGER_URL)
    time.sleep(6)
    cards: list = []
    for _ in range(20):
        cards = json.loads(page.evaluate(CARD_TIME_JS))
        if cards:
            break
        time.sleep(0.5)

    hits = [c for c in cards if args.title in (c.get("title") or "")]
    if not hits:
        print(f"笔记管理里没有标题含 {args.title!r} 的卡片（扫到 {len(cards)} 张）。"
              "刚提交的话等一下再查；列表是虚拟滚动的，较老的要往下翻才看得到。",
              file=sys.stderr)
        return 1

    want_txt = want.strftime("%Y-%m-%d %H:%M")
    for c in hits:
        print(f"{'✅' if c['time'] == want_txt else '❌'} "
              f"{c['time'] or '(无时间)'}  {c['title'][:40]}  {c['id']}")
    if any(c["time"] == want_txt for c in hits):
        print(f"已确认：卡片上的时间是 {want_txt}")
        return 0
    print(f"没对上：期望 {want_txt}，卡片上是 {hits[0]['time'] or '(空)'}。"
          "超窗的时刻会被平台退回默认值——把这条删掉，等窗口放开再排。",
          file=sys.stderr)
    return 1


def cmd_delete_note(args) -> int:
    """按 noteId 删掉一条笔记（定时未发的也算）。

    平台把删除确认放进 Vue portal：弹窗**关闭后仍留在 DOM**（淡出），且容器是
    position:fixed（offsetParent 恒为 null，不能拿它判可见）。判定"这次真的弹出来了"
    用 VISIBLE_FN_JS。定位只认 noteId：标题会重复（替换时新旧会短暂共存），列表顺序也会变。
    """
    page, _ = bridge()
    note_id = (args.id or "").strip()
    if not note_id:
        die("--id 不能为空")

    page.navigate(NOTE_MANAGER_URL)
    time.sleep(6)

    cards: list = []
    for _ in range(20):
        cards = json.loads(page.evaluate(NOTE_CARDS_JS))
        if cards:
            break
        time.sleep(0.5)
    hit = next((c for c in cards if c["id"] == note_id), None)
    if not hit:
        die(f"笔记管理里找不到 noteId={note_id}（当前扫到 {len(cards)} 张卡）")

    label = hit["text"][:60]
    if args.title and args.title not in hit["text"]:
        die(f"标题不符：noteId={note_id} 的卡是「{label}」，不含 {args.title!r}"
            "——防误删已停下")
    print(f"目标：{label}")

    if not args.yes:
        print("删除不可恢复。核对无误后加 --yes 重跑。", file=sys.stderr)
        return 1

    # 先关掉可能残留的旧弹窗，免得下一步点到别的笔记的「确定」。
    page.evaluate("(() => {" + VISIBLE_FN_JS + """
      for (const m of document.querySelectorAll('.d-modal.modal-container')) {
        if (!visible(m)) continue;
        for (const b of m.querySelectorAll('button')) {
          if ((b.textContent || '').trim() === '取消') { b.click(); return; }
        }
      }
    })()""")
    time.sleep(1)

    clicked = page.evaluate("""(() => {
      for (const c of document.querySelectorAll('.note-card')) {
        let id = '';
        try {
          id = JSON.parse(c.getAttribute('data-impression') || '{}')
                 .noteTarget.value.noteId;
        } catch (e) {}
        if (id !== %s) continue;
        const b = c.querySelector('.note-card__action-btn--del');
        if (!b) return 'no-del-btn';
        b.click();
        return 'clicked';
      }
      return 'no-card';
    })()""" % json.dumps(note_id))
    if clicked != "clicked":
        die(f"删除按钮没点上：{clicked}")

    # 弹窗是异步挂上来的：要轮询等它可见，点完立刻找会扑空。
    info: dict = {}
    for _ in range(24):
        time.sleep(0.5)
        info = json.loads(page.evaluate(DELETE_MODAL_JS))
        if info:
            break
    if not info:
        die("删除确认弹窗没出现（.confirm-button 不可见）——页面可能改版")
    if "删除" not in info["text"]:
        die(f"弹窗文案不像删除确认，已停下：{info['text']!r}")

    # `.click()` 在这套组件上不稳，按真鼠标坐标点。
    page.mouse_click(info["x"], info["y"])
    time.sleep(2)
    for _ in range(20):
        time.sleep(0.5)
        ids = [c["id"] for c in json.loads(page.evaluate(NOTE_CARDS_JS))]
        if note_id not in ids:
            print(f"已删除 {note_id}（列表剩 {len(ids)} 张卡，已无此 id）")
            return 0
    die(f"点了确认但 {note_id} 仍在列表里——去页面手动看一眼，先别重复点")


def cmd_shot(args) -> int:
    if sys.platform != "darwin":
        die("shot 目前只支持 macOS")
    out = args.out
    for _ in range(4):
        subprocess.run(["open", "-a", "Google Chrome"], check=False)
        time.sleep(1.5)
        front = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first application process '
             'whose frontmost is true'],
            capture_output=True, text=True).stdout.strip()
        if front == "Google Chrome":
            subprocess.run(["screencapture", "-x", out], check=True)
            print("已截图：", out)
            return 0
    die("Chrome 不是前台窗口，截图会抓到别的应用（Codex/ChatGPT 会抢焦点）")


def main() -> int:
    ap = argparse.ArgumentParser(description="通过浏览器桥接操作小红书发布页")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="检查桥接与登录状态").set_defaults(fn=cmd_status)
    sub.add_parser("reset", help="打开干净的发布页").set_defaults(fn=cmd_reset)

    p = sub.add_parser("fill", help="上传图片并填写标题正文")
    p.add_argument("--dir", required=True, help="post 目录（含图片与 title/body 文件）")
    p.add_argument("--title", default="title.txt")
    p.add_argument("--body", default="body.txt")
    p.add_argument("--collection", default=None, help="填写后顺便加入的合集名")
    p.set_defaults(fn=cmd_fill)

    p = sub.add_parser("tags", help="把正文末尾的 #标签 提交成话题（可重跑）")
    p.add_argument("--dir", required=True, help="post 目录（含 body 文件）")
    p.add_argument("--body", default="body.txt")
    p.add_argument("--tags", nargs="*", default=None, help="直接指定标签，覆盖文件")
    p.set_defaults(fn=cmd_tags)

    p = sub.add_parser("collection", help="加入合集")
    p.add_argument("name")
    p.set_defaults(fn=cmd_collection)

    sub.add_parser("original", help="打开原创声明（含须知弹窗）").set_defaults(
        fn=cmd_original)

    p = sub.add_parser("schedule", help="打开定时发布并设置时间")
    p.add_argument("at", help="发布时间，格式 YYYY-MM-DD HH:MM")
    p.set_defaults(fn=cmd_schedule)

    p = sub.add_parser("verify", help="回读表单并比对")
    p.add_argument("--dir", default=None)
    p.add_argument("--title", default="title.txt")
    p.add_argument("--body", default="body.txt")
    p.add_argument("--collection", default=None)
    p.set_defaults(fn=cmd_verify)

    sub.add_parser("publish", help="点击发布").set_defaults(fn=cmd_publish)

    p = sub.add_parser("check-published", help="确认已发布")
    p.add_argument("title")
    p.set_defaults(fn=cmd_check_published)

    p = sub.add_parser("delete-note", help="按 noteId 删除一条笔记（不可恢复）")
    p.add_argument("--id", required=True, help="要删除的笔记 noteId")
    p.add_argument("--title", default=None, help="可选：断言卡片标题含此串，防误删")
    p.add_argument("--yes", action="store_true", help="确认删除（不加只报告目标）")
    p.set_defaults(fn=cmd_delete_note)

    p = sub.add_parser("verify-scheduled", help="回读卡片上的定时时间并比对")
    p.add_argument("--title", required=True, help="卡片标题（可只给一部分）")
    p.add_argument("--expect", required=True, help="期望的定时时间 YYYY-MM-DD HH:MM")
    p.set_defaults(fn=cmd_verify_scheduled)

    p = sub.add_parser("shot", help="给用户看截图（macOS）")
    p.add_argument("--out", default="/tmp/xhs-shot.png")
    p.set_defaults(fn=cmd_shot)

    args = ap.parse_args()
    if args.cmd != "status":
        ensure_server()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
