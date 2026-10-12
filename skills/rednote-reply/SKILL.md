---
name: rednote-reply
description: >-
  小红书互动回复：读私信（聊天）会话，按账号人设拟一句回话，经用户确认后再发出去并回读核验。
  私信优先；评论入口尚未打通。默认只读——发送要用户逐条点头，绝不自动发。
  当用户说"回了这条私信""粉丝问……怎么回""看看私信""有没有人找我""回一下这个人"
  "rednote 回复"时激活。
  不适用于：写笔记正文与做卡片图（走 rednote-post）、排期与定时发布（走 rednote-assistant）。
metadata:
  short-description: 小红书私信只读巡检 + 按人设拟稿，确认后再发
---

# 小红书互动回复

只管**别人说了什么、这句该怎么回、发出去没有**。写笔记走 `rednote-post`，
排期走 `rednote-assistant`；本 skill 不选题、不做图、不改已发布内容。

下面命令里的 `<skill>` 指本文件所在目录
（通常是 `${CODEX_HOME:-$HOME/.codex}/skills/rednote-reply`）。
按字面写 `scripts/reply.py` 会找不到——当前工作目录通常是用户的项目，不是 skill 目录。

## 三个命令

| 要什么 | 命令 | 动没动手 |
| --- | --- | --- |
| 谁找过我（名字／时间／末条预览） | `python3 <skill>/scripts/reply.py list` | 只读 |
| 这个会话到底说了什么 | `python3 <skill>/scripts/reply.py read --id <convId>`（或 `--name 昵称`） | 只读 |
| 发一条 | `python3 <skill>/scripts/reply.py send --id <convId> --text "..."` | **默认 dry-run**，加 `--yes` 才真发 |

## 铁律

1. **默认只读；发送要用户逐条点头。** `send` 不带 `--yes` 只把将要说的话打印出来、返回 1。
   每一次发送都是单独一次授权——上一轮同意过，不等于这一轮同意。
2. **先读人设再动笔。** 说话口径在 [`references/soul.md`](references/soul.md)，动笔前先读它。
   用户当次说了别的口气，以用户当次说的为准。
3. **私信不是笔记。** 不排版、不加标签、不写出处行、不 emoji 轰炸。一到两句，
   先答问题，念得出声。
4. **不确定就问用户，别替他说。** 见下面「什么情况下必须停下来问」。
5. **发完回读。** 发出去之后重新读一遍这个会话，确认新的一条真在里面、内容一致；
   对不上就照实说，别报"已发送"。

## 账号

账号是 **司马读书**（自称「老司马」，英文 Simon）。事实、口气、边界见
[`references/soul.md`](references/soul.md)。笔记口径另有一份
[`../rednote-post/references/persona.md`](../rednote-post/references/persona.md)——
**聊天短、笔记长，两份别混用。**

## 页面与选择器

私信页是 `https://www.xiaohongshu.com/chat`（列表）和 `/chat/<convId>`（单个会话）。
**别用 `/messages`**——那条路由缺 `xsec_token`，会被 302 到 `/404`（2026-10-11 实测）。
选择器与两个坑见 [`references/platform-selectors.md`](references/platform-selectors.md)。

桥接就是 `rednote-post` 那一个（Chrome 扩展 → **当前活动标签页**）。跑之前先
`python3 ../rednote-post/scripts/xhs.py status` 确认扩展已连接、登录态是司马读书；
命令跑起来时别切走 Chrome。

## 工作流

1. `list` 看谁找过来了。
2. `read` 把要回的那个会话读全——它分得清 我／对方／系统，也标得出表情和图片。
3. 读 [`references/soul.md`](references/soul.md)，按它拟 **1–3 个版本**给用户挑。
   **不要自己挑一版就发**，也不要一次列十版。
4. 用户定了哪一版，`send` 先 dry-run 给他看一眼，再加 `--yes`；发完回读确认。
5. 一句话汇报：给谁、对方原话、发了什么、回读结果。

## 什么情况下必须停下来问

- 涉及**钱／合作／带货／代理／报价**。
- 要**联系方式**（微信、电话、邮箱），或要把人**拉群**。
- **隐私**（地址、证件、家庭信息），或对方在**发泄／骂人／情绪很重**。
- **医疗、法律、投资、政治**这类说错了要担责的。
- 对方要求**删帖／改稿／授权转载**。
- 平台提示**风险、验证、异地登录**。
- 桥接不通、登错账号、页面改了导致读出来的东西对不上。

宁可不回，也不要替他说一句他没说过的话。

## 已知边界

- **只有私信打通了。** 评论／@ 的后台入口还没找到（`/interactive/comment` 是 404），
  要做评论得先定位真实路由。
- **发送只有一条姿势**（2026-10-11 实测、已发过）：没有发送按钮，
  `press_key("Enter")` 只会插换行。得真打字进输入框，再在页内派发带 `keyCode 13`
  的 Enter 键盘事件——`reply.py` 里已经这么做了，别绕开它自己写。
- **这是别人的消息。** 不要把会话内容写进笔记、转给第三方，或存进仓库。

