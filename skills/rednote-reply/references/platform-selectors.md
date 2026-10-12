# 私信页的真实选择器（2026-10-11 实测）

这份管**主站的私信页**（`www.xiaohongshu.com/chat`）。
创作者中心（笔记管理／发布页）那份在
[`../../rednote-assistant/references/platform-selectors.md`](../../rednote-assistant/references/platform-selectors.md)，
别拿错。平台改版时照本表修。

## 路由

| 用途 | URL | 备注 |
| --- | --- | --- |
| 会话列表 | `https://www.xiaohongshu.com/chat` | 只挂列表，不带消息窗 |
| 单个会话 | `https://www.xiaohongshu.com/chat/<convId>` | convId 形如 `59f6f010e8ac2b675f25585f` |
| ~~消息页~~ | ~~`/messages`~~ | **走不通**：缺 `xsec_token` → 302 → `/404?source=/messages` |

## 元素

| 东西 | 选择器 | 说明 |
| --- | --- | --- |
| 会话项 | `.xhs-im-conv-item` | 当前打开的那个多一个 `--active`；`innerText` 是 名字／时间／末条预览 三行（有的只有两行） |
| 对方名字 | `.xhs-im-chat-window__header-name` | 消息窗顶部 |
| 消息列表 | `.xhs-im-msg-list` | **直接子节点**才是消息与时间分隔 |
| 时间分隔 | `.xhs-im-msg-list__time-divider` | 「昨天 14:32」这种 |
| 单条消息 | `.chat-item` | 气泡是里面的 `.chat-item__bubble` |
| **谁说的** | 气泡上的 `chat-item__bubble--me` / `--other` | **唯一可靠的分法**——`.chat-item` 本身占满整行，按左右坐标分不出来（踩过一次） |
| 表情消息 | `chat-item__bubble--emoji` | 气泡 `innerText` 往往是空的，得看图 |
| 输入框 | `.xhs-im-input-bar-editor` | `contenteditable="true"`，`data-placeholder="发消息..."` |
| 发送按钮 | **没有** | `xhs-im-input-bar-actions` 里只有"表情"（`title="表情"`）；那个 `<!---->` 占位填了字也不渲染。发送只能靠 Enter |

## 三个坑

1. **`convId` 不在列表的 DOM 里。** 会话项是 SPA 组件，不暴露 id——
   想知道 id 就点开它，再读 `location.href`。
2. **点开会话要等。** 切会话后消息窗是异步灌的，导航完马上抓会抓到上一个会话的残影。
   等 `.xhs-im-msg-list` 挂出来再抓（`reply.py` 里已经等了）。
3. **按昵称点会话，必须用真鼠标坐标。** `.click()` / 合成事件打不动这套 Vue 组件——
   点完之后路由不动、URL 还停在 `/chat`，看起来"点了没反应"（2026-10-11 实测）。
   取 `.xhs-im-conv-item` 的中心坐标走 `mouse_move` + `mouse_click` 才进得去。
   `xhs.py` 的删除确认弹窗踩过同一个坑。
4. **发送要"真打字 + 页内派发 Enter"。**（2026-10-11 实测）
   - 直接改 `.innerHTML` 塞进去的字，Vue 收不到——字得用真键盘敲进 `activeElement`。
   - `press_key("Enter")`（走 `chrome.debugger` / CDP）**不管用**：它的 keyDown 不带
     `text`，页面只当插入一个换行，消息发不出去，输入框里留下一个 `<div><br></div>`。
   - 有效的是：聚焦输入框 → 真打字 → 在页内派发 `keydown`/`keypress`/`keyup`，
     带上 `key: 'Enter'` 和 `keyCode: 13`。发完输入框清空，消息末尾是"刚刚"。

