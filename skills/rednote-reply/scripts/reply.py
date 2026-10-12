#!/usr/bin/env python3
"""读、拟、发小红书私信——**发送默认 dry-run**。

和 rednote-post/scripts/xhs.py 共用同一个浏览器桥接
（Chrome 扩展 → 当前活动标签页）。跑之前先确认扩展已连接、登录态是司马读书。

子命令：
  list                        会话列表（名字 / 时间 / 末条预览）
  read   (--id ID | --name X)  读一个会话的完整往返（区分 我 / 对方 / 系统）
  send   (--id ID | --name X) --text "..."   发一条；**不加 --yes 只 dry-run**

convId 就是 https://www.xiaohongshu.com/chat/<convId> 里那串。

实测（2026-10-11）：/chat 与 /chat/<id> 可达，/messages 被 302 到 /404。
**发送**只有一条路，而且挑姿势：真键盘打字进输入框，再在页内派发一个带
`keyCode 13` 的 Enter 键盘事件。`press_key("Enter")` 和直接改 innerHTML 都不行，
细节见下面 ENTER_SEND_JS 的注释。选择器见 references/platform-selectors.md。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("XHS_SKILLS_DIR", "/tmp/xhs-skills-repo"))
BRIDGE_URL = os.environ.get("XHS_BRIDGE_URL", "ws://localhost:9333")
CHAT_URL = "https://www.xiaohongshu.com/chat"

CONV_SEL = ".xhs-im-conv-item"
MSG_LIST_SEL = ".xhs-im-msg-list"
EDITOR_SEL = ".xhs-im-input-bar-editor"


# 发送只能这么干：真打字 + 在页内派发 Enter 的 keydown。
# 这条路上踩过三个坑（都是 2026-10-11 实测）：
#   · **没有发送按钮**——输入框为空时 `.xhs-im-input-bar-actions` 里只有"表情"按钮，
#     那个 `<!---->` 占位填了字也不渲染；
#   · **`press_key("Enter")` 不管用**（走 chrome.debugger / CDP）——它的 keyDown 不带
#     `text`，页面只当插入一个换行，消息发不出去（输入框里留个 `<div><br></div>`）；
#   · **直接改 `.innerHTML` 写进去的字，Vue 收不到**——所以字要用真键盘敲。
# 合起来：先 `type_text` 打进输入框，再派发带 keyCode 13 的键盘事件。
ENTER_SEND_JS = r"""(() => {
  const e = document.querySelector('.xhs-im-input-bar-editor');
  if (!e) throw new Error('输入框不存在');
  const o = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
             bubbles: true, cancelable: true};
  for (const t of ['keydown', 'keypress', 'keyup']) e.dispatchEvent(new KeyboardEvent(t, o));
  return true;
})()"""

EDITOR_CLEAR_JS = r"""(() => {
  const e = document.querySelector('.xhs-im-input-bar-editor');
  if (!e) return false;
  e.innerHTML = '';
  e.dispatchEvent(new InputEvent('input', {bubbles: true}));
  return true;
})()"""

EDITOR_FOCUS_JS = r"""(() => {
  const e = document.querySelector('.xhs-im-input-bar-editor');
  if (!e) return null;
  e.focus();
  return (document.activeElement || {}).className || null;
})()"""

EDITOR_TEXT_JS = "(document.querySelector('.xhs-im-input-bar-editor') || {}).innerText || ''"


def reexec_in_venv() -> None:
    """桥接要 `websockets`，它在 skills 仓库的 venv 里。换过去跑，调用方随便用哪个 python。"""
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


LIST_JS = r"""(() => JSON.stringify(
  [...document.querySelectorAll('.xhs-im-conv-item')].map(e => {
    const t = (e.innerText || '').trim().split('\n').map(s => s.trim()).filter(Boolean);
    return {name: t[0] || null, time: t[1] || null,
            preview: t.slice(2).join(' ') || null,
            active: /--active/.test(e.className || '')};
  })
))()"""

# 按昵称定位会话项的中心坐标。会话项上**必须用真鼠标点**：
# `.click()` 打不动这套 Vue 组件——合成点击之后路由不动，URL 还停在 /chat
# （2026-10-11 实测）。xhs.py 的删除确认弹窗踩过同一个坑。
FIND_CONV_JS = r"""(() => {
  const name = %s;
  const items = [...document.querySelectorAll('.xhs-im-conv-item')];
  const first = (e) => ((e.innerText || '').split('\n')[0] || '').trim();
  const hit = items.find(e => first(e) === name) || items.find(e => (e.innerText || '').includes(name));
  if (!hit) return '{}';
  const r = hit.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return '{}';
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2});
})()"""

# 谁说的只能看气泡上的 --me / --other：.chat-item 占满整行，按左右坐标分不出来。
READ_JS = r"""(() => {
  const list = document.querySelector('.xhs-im-msg-list');
  if (!list) return JSON.stringify({error: '消息列表还没挂出来'});
  const out = [];
  [...list.children].forEach(k => {
    const c = (k.className || '').toString();
    if (c.includes('time-divider')) { out.push({kind: 'time', text: k.innerText.trim()}); return; }
    if (!c.includes('chat-item')) return;
    const b = k.querySelector('.chat-item__bubble');
    if (!b) return;
    const bc = b.className.toString();
    const text = (b.innerText || '').trim();
    const row = {
      kind: bc.includes('--emoji') ? 'emoji' : (bc.includes('--image') ? 'image' : 'text'),
      who: bc.includes('--me') ? 'me' : 'other',
      text: text,
    };
    if (!text) row.images = [...k.querySelectorAll('img')].map(i => i.src);
    out.push(row);
  });
  return JSON.stringify({
    conv_id: (location.pathname.match(/\/chat\/([^/?#]+)/) || [])[1] || null,
    name: (document.querySelector('.xhs-im-chat-window__header-name') || {}).innerText || null,
    messages: out,
  }, null, 1);
})()"""


def die(msg: str, code: int = 2):
    print(f"错误：{msg}", file=sys.stderr)
    sys.exit(code)


def bridge():
    sys.path.insert(0, str(SKILLS_DIR / "scripts"))
    try:
        from xhs.bridge import BridgePage
    except ImportError as e:
        die(f"无法从 {SKILLS_DIR} 导入 xhs 模块：{e}\n"
            "先按 rednote-post/references/publishing.md 配好桥接，"
            "或用 XHS_SKILLS_DIR 指向 skills 仓库。")
    try:
        return BridgePage(BRIDGE_URL)
    except Exception as e:  # noqa: BLE001
        die(f"连不上桥接 {BRIDGE_URL}：{e}\n"
            "先跑 python3 ../rednote-post/scripts/xhs.py status 看扩展与登录态。")


def open_conversation(page, conv_id: str | None, name: str | None) -> bool:
    """打开目标会话。用 id 直接导航；只给了昵称就先开列表、点开它。

    切会话是异步的，所以等 .xhs-im-msg-list 真挂出来再返回——否则会读到上一个会话的残影。
    """
    if conv_id:
        page.navigate(f"{CHAT_URL}/{conv_id}")
    else:
        page.navigate(CHAT_URL)
        page.wait_for_element(CONV_SEL, timeout=20.0)
        time.sleep(0.8)  # 列表是渲染出来的，等它挂稳再取坐标
        info = json.loads(page.evaluate(FIND_CONV_JS % json.dumps(name)))
        if not info:
            die(f"会话列表里没找到「{name}」。先跑 list 看看昵称怎么写。", 1)
        page.mouse_move(info["x"], info["y"])
        time.sleep(0.2)
        page.mouse_click(info["x"], info["y"])
    try:
        page.wait_for_element(MSG_LIST_SEL, timeout=20.0)
    except Exception:  # noqa: BLE001
        return False
    time.sleep(1.2)
    return True


def read_conversation(page) -> dict:
    return json.loads(page.evaluate(READ_JS))


def focus_editor(page) -> bool:
    """把焦点放进输入框，并确认真的进去了。

    用 JS `focus()`，**不用鼠标点**：鼠标点在这套组件上不稳——同一段代码
    2026-10-11 上午点进去了、下午就没把 activeElement 挪过来（点下去不报错，
    但焦点还在 `ai-layout-active` 上）。`type_text` 打的是 `document.activeElement`，
    焦点不在输入框就一个字都打不进去——而"一个字都没打进去"和"打进去了"长得一样，
    所以这里必须回读 activeElement 确认，不能只看调用有没有抛错。
    """
    cls = page.evaluate(EDITOR_FOCUS_JS) or ""
    return "xhs-im-input-bar-editor" in cls


def need_target(args) -> None:
    if not args.id and not args.name:
        die("要么 --id <convId>，要么 --name <昵称>。", 2)


def cmd_list(args) -> int:
    page = bridge()
    page.navigate(CHAT_URL)
    page.wait_for_element(CONV_SEL, timeout=20.0)
    time.sleep(1.0)
    rows = json.loads(page.evaluate(LIST_JS))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for i, r in enumerate(rows, 1):
        print(f"{'*' if r['active'] else ' '} {i:>2}  {r['name'] or '?'}  "
              f"[{r['time'] or ''}]  {(r['preview'] or '').strip()[:40]}")
    print(f"\n共 {len(rows)} 个会话（* 是当前打开的那个）。"
          "要看某个会话：reply.py read --name <昵称>", file=sys.stderr)
    return 0


def cmd_read(args) -> int:
    need_target(args)
    page = bridge()
    if not open_conversation(page, args.id, args.name):
        die("没等到消息列表——会话没打开，或页面还在加载。", 1)
    print(json.dumps(read_conversation(page), ensure_ascii=False, indent=1))
    return 0


def cmd_send(args) -> int:
    need_target(args)
    text = args.text.strip()
    if not text:
        die("--text 是空的。", 2)
    page = bridge()
    if not open_conversation(page, args.id, args.name):
        die("没等到消息列表——会话没打开，或页面还在加载。", 1)
    data = read_conversation(page)
    who = data.get("name") or args.name or args.id
    if not page.has_element(EDITOR_SEL):
        die("找不到输入框——页面结构可能改了，先核对 references/platform-selectors.md。", 1)

    if not args.yes:
        print(f"dry-run —— 收件人：{who}（{data.get('conv_id')}）")
        print("将要说的话：")
        print(f"  {text}")
        print("\n确认无误后加 --yes 真发。")
        return 1

    page.evaluate(EDITOR_CLEAR_JS)
    time.sleep(0.3)
    if not focus_editor(page):
        die("焦点没进输入框，没发。", 1)
    page.type_text(text, delay_ms=30)
    time.sleep(0.8)
    typed = (page.evaluate(EDITOR_TEXT_JS) or "").strip()
    if typed != text:
        die(f"输入框里的字和要说的话对不上（读到 {typed!r}），没发。", 1)

    page.evaluate(ENTER_SEND_JS)
    time.sleep(2.0)
    after = read_conversation(page)
    tail = [m for m in after.get("messages", []) if m.get("kind") != "time"][-1:]
    ok = bool(tail) and tail[0].get("who") == "me" and (tail[0].get("text") or "").strip() == text
    print(("已发送 ✓ " if ok else "回读没对上 ✗ ") + f"—— {who}")
    print(json.dumps(tail, ensure_ascii=False, indent=1))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="读 / 发小红书私信（默认只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列会话（只读）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    for name, fn in (("read", cmd_read), ("send", cmd_send)):
        p = sub.add_parser(name, help="读一个会话（只读）" if name == "read" else "发一条（默认 dry-run）")
        p.add_argument("--id", default=None, help="convId（/chat/<id> 里那串）")
        p.add_argument("--name", default=None, help="按昵称点开（给了 --id 就用 --id）")
        if name == "send":
            p.add_argument("--text", required=True, help="要发的话")
            p.add_argument("--yes", action="store_true", help="真发出去；不加只 dry-run")
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

